"""Sequential MT5 optimization with untouched input sets and XML reports."""

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET

from mt5_batch_backtest import MODEL_MIGRATE, TF_ENUM_TO_PERIOD, PERIOD_MAP

METHODS = {'2': 'Fast genetic algorithm', '1': 'Slow complete algorithm'}
CRITERIA = {
    '0': 'Balance max (Balance max)',
    '1': 'Balance × profitability (Profit Factor max)',
    '2': 'Balance × expected payoff (Expected Payoff max)',
    '3': 'Balance × (100% − drawdown) (Drawdown min)',
    '4': 'Balance × recovery factor (Recovery Factor max)',
    '5': 'Balance × Sharpe ratio (Sharpe Ratio max)',
    '6': 'Custom OnTester criterion (Custom max)',
    '7': 'Complex criterion max (Complex Criterion max)',
}
PERIODS = sorted(set(TF_ENUM_TO_PERIOD.values()) | set(PERIOD_MAP.values()))
EXCLUDED = {'_batch_modified', 'optimisationreport', 'reports'}


def discover_sets(folder, recursive=True):
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d.lower() not in EXCLUDED and not d.startswith('.'))
        found.extend(str(Path(root, name).resolve()) for name in sorted(files)
                     if name.lower().endswith('.set'))
        if not recursive:
            break
    return sorted(found)


def read_inputs(path):
    raw = Path(path).read_bytes()
    encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
    lines = raw.decode(encoding).splitlines()
    return [line.strip() for line in lines
            if line.strip() and not line.lstrip().startswith(';') and '=' in line]


def optimized_inputs(path):
    return [line.split('=', 1)[0] for line in read_inputs(path)
            if len(line.split('||')) == 5 and line.split('||')[-1].upper() == 'Y']


def validate_job(job, cfg, mode='optimize'):
    if mode not in ('optimize', 'backtest'):
        raise ValueError('Mode must be optimize or backtest.')
    if not read_inputs(job['path']):
        raise ValueError('Set file contains no EA inputs.')
    if mode == 'optimize' and not optimized_inputs(job['path']):
        raise ValueError('No enabled optimization inputs (||Y) in this set file.')
    if not job['symbol'] or any(c in job['symbol'] for c in '\r\n='):
        raise ValueError('A valid symbol is required.')
    if job['period'] not in PERIODS:
        raise ValueError('Choose a supported timeframe.')
    if mode == 'optimize' and str(cfg.get('optimization')) not in METHODS:
        raise ValueError('Choose genetic or complete optimization.')
    if str(cfg.get('optimization_criterion')) not in CRITERIA:
        raise ValueError('Choose an optimization criterion.')


def report_path(job, cfg, mode='optimize'):
    # Include inputs and run settings so resume cannot silently reuse a different run.
    identity = {k: cfg.get(k) for k in (
        'ea_name', 'terminal_path', 'tester_folder', 'from_date', 'to_date',
        'model', 'deposit', 'currency', 'leverage', 'optimization', 'optimization_criterion')}
    identity.update(symbol=job['symbol'], period=job['period'])
    if mode == 'backtest':
        identity['mode'] = mode
    digest = hashlib.sha256(Path(job['path']).read_bytes() +
                            json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    stem = re.sub(r'[<>:"/\\|?*\s]', '_', Path(job['path']).stem)[:100]
    if mode == 'backtest':
        model = MODEL_MIGRATE.get(str(cfg['model']), str(cfg['model']))
        label = {'0': 'EVERYTICK', '1': 'OHLC', '2': 'OPENPRICES', '4': 'REALTICKS'}[model]
        return Path(job['path']).parent / 'reports' / f'{stem}_{label}_{digest}.htm'
    return Path(job['path']).parent / 'OptimisationReport' / f'{stem}_{digest}.xml'


def valid_report(path, mode='optimize'):
    try:
        if mode == 'backtest':
            raw = Path(path).read_bytes()
            encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
            content = raw.decode(encoding).lower()
            return '<html' in content and '</html>' in content and '<table' in content
        root = ET.parse(path).getroot()
        return root.tag.rsplit('}', 1)[-1] == 'Workbook' and any(
            elem.tag.rsplit('}', 1)[-1] == 'Worksheet' for elem in root)
    except (OSError, ET.ParseError, UnicodeError):
        return False


def build_ini(job, cfg, set_name, report_name, mode='optimize'):
    validate_job(job, cfg, mode)
    values = {
        'Expert': cfg['ea_name'], 'ExpertParameters': set_name,
        'Symbol': job['symbol'], 'Period': job['period'],
        'Optimization': cfg['optimization'] if mode == 'optimize' else '0',
        'OptimizationCriterion': cfg['optimization_criterion'],
        'Model': MODEL_MIGRATE.get(str(cfg['model']), str(cfg['model'])),
        'FromDate': cfg['from_date'], 'ToDate': cfg['to_date'],
        'ForwardMode': '0', 'Deposit': cfg['deposit'], 'Currency': cfg['currency'],
        'Leverage': f'1:{cfg["leverage"]}', 'ProfitInPips': '0',
        'ExecutionMode': '0', 'Visual': '0', 'UseLocal': '1',
        'UseRemote': '0', 'UseCloud': '0', 'Report': report_name,
        'ReplaceReport': '1', 'ShutdownTerminal': '1',
    }
    if any('\n' in str(v) or '\r' in str(v) for v in values.values()):
        raise ValueError('Settings must not contain line breaks.')
    return '[Tester]\r\n' + ''.join(f'{k}={v}\r\n' for k, v in values.items())


def terminal_running(terminal_path):
    # Looking up a missing process by name makes PowerShell exit with code 1,
    # even with SilentlyContinue. Enumerate first so no matches is a success.
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         "$ErrorActionPreference = 'Stop'; try { "
         "$terminalPaths = @(Get-Process | "
         "Where-Object { $_.ProcessName -eq 'terminal64' } | "
         "ForEach-Object { $_.Path }); "
         "ConvertTo-Json -InputObject $terminalPaths -Compress; exit 0 "
         "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=20)
    if result.returncode:
        detail = result.stderr.strip() or f'PowerShell exited with code {result.returncode}.'
        raise RuntimeError(f'Could not check whether MT5 is running: {detail}')
    paths = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(paths, str):
        paths = [paths]
    return any(not p or os.path.normcase(p) == os.path.normcase(terminal_path) for p in paths)


class BatchRun:
    def __init__(self):
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.thread = None

    def start(self, jobs, cfg, skip_existing=False, timeout_hours=24):
        self.thread = threading.Thread(target=run_batch,
            args=(jobs, dict(cfg), self.events, self.stop, skip_existing, timeout_hours), daemon=True)
        self.thread.start()


def run_batch(jobs, cfg, events, stop, skip_existing=False, timeout_hours=24, mode='optimize'):
    try:
        terminal = Path(cfg['terminal_path'])
        data = Path(cfg['tester_folder']).parent
        profiles = data / 'MQL5' / 'Profiles' / 'Tester'
        for idx, job in enumerate(jobs):
            if stop.is_set():
                break
            status = {'idx': idx, 'file': job['path']}
            generated = []
            proc = None
            try:
                validate_job(job, cfg, mode)
                destination = report_path(job, cfg, mode)
                if skip_existing and valid_report(destination, mode):
                    events.put(dict(status, status='skipped', message='Matching report already exists', report=str(destination)))
                    continue
                if not terminal.is_file() or not (data / 'MQL5').is_dir():
                    raise ValueError('Check the terminal executable and Tester folder in Active Config.')
                if terminal_running(str(terminal)):
                    raise RuntimeError('The selected MT5 terminal is already open. Close it before starting a batch.')
                profiles.mkdir(parents=True, exist_ok=True)
                token = 'batch_opt_' + uuid.uuid4().hex
                staged = profiles / (token + '.set')
                ini = profiles / (token + '.ini')
                generated.extend([staged, ini])
                shutil.copy2(job['path'], staged)  # Preserve every byte, including ranges and flags.
                ini.write_bytes(build_ini(job, cfg, staged.name, token, mode).encode('utf-16'))
                events.put(dict(status, status='running', message='Optimizing…' if mode == 'optimize' else 'Backtesting…'))
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 6
                proc = subprocess.Popen([str(terminal), f'/config:{ini}'], startupinfo=si)
                deadline = time.monotonic() + timeout_hours * 3600
                while proc.poll() is None:
                    if time.monotonic() >= deadline:
                        proc.terminate()
                        proc.wait(timeout=30)
                        raise TimeoutError('Optimization exceeded the configured time limit.')
                    time.sleep(1)
                if proc.returncode:
                    raise RuntimeError(f'MT5 exited with code {proc.returncode}. Check its tester journal.')
                extension = '.xml' if mode == 'optimize' else '.htm'
                candidates = list(dict.fromkeys(
                    folder / (token + extension) for folder in
                    [data, terminal.parent, profiles, Path(cfg['tester_folder'])]))
                source = next((p for p in candidates if valid_report(p, mode)), None)
                if source is None:
                    raise RuntimeError(f'No valid {mode} report was produced. Check the MT5 tester journal.')
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix('.' + token + '.tmp')
                generated.append(temporary)
                if mode == 'backtest':
                    # MT5 charts refer to the temporary report name. Rename only
                    # this run's image references while copying companion charts.
                    raw = source.read_bytes()
                    encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
                    content = raw.decode(encoding)
                    companions = list(source.parent.glob(token + '*.png'))
                    for companion in companions:
                        target = destination.parent / (destination.stem + companion.name[len(token):])
                        shutil.copy2(companion, target)
                        content = content.replace(companion.name, target.name)
                    temporary.write_bytes(content.encode(encoding))
                else:
                    companions = []
                    shutil.copy2(source, temporary)
                os.replace(temporary, destination)
                generated.extend([source, *companions])
                events.put(dict(status, status='done', message='Report saved', report=str(destination)))
            except Exception as exc:
                events.put(dict(status, status='failed', message=str(exc)))
                # Never launch another terminal if an owned process could still be running.
                if proc is not None and proc.poll() is None:
                    break
            finally:
                for path in generated:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
    except Exception as exc:
        events.put({'status': 'error', 'message': str(exc)})
    finally:
        events.put({'status': 'complete', 'stopped': stop.is_set()})
