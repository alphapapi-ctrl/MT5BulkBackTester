"""Non-interactive MT5 batch backtesting and optimization. No Streamlit required."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import uuid

from mt5_batch_backtest import DEFAULTS, MODEL_MIGRATE, get_ubs_symbol, get_ubs_period, detect_timeframe
import mt5_batch_optimizer as runner

ROOT = Path(__file__).resolve().parent
MODELS = {'ohlc': '1', 'everytick': '0', 'realticks': '4', 'openprices': '2',
          '0': '0', '1': '1', '2': '2', '4': '4'}


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('mode', choices=['backtest', 'optimize'])
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--folder', type=Path, help='Root folder of .set files')
    source.add_argument('--jobs', type=Path, help='JSON list of {path, symbol, period}; relative paths are relative to this JSON file')
    p.add_argument('--config', type=Path, default=ROOT / 'mt5_batch_config.json', help='Read-only JSON settings; command-line settings take precedence')
    for flag, dest in [('terminal', 'terminal_path'), ('tester-folder', 'tester_folder'), ('ea', 'ea_name'),
                       ('from-date', 'from_date'), ('to-date', 'to_date'), ('deposit', 'deposit'),
                       ('currency', 'currency'), ('leverage', 'leverage'), ('suffix', 'suffix')]:
        p.add_argument('--'+flag, dest=dest, default=None)
    p.add_argument('--model', choices=list(MODELS))
    p.add_argument('--method', choices=['complete', 'genetic'], help='Required for optimize unless supplied as optimization=1 or 2 in config')
    p.add_argument('--criterion', choices=list(runner.CRITERIA), help='MT5 criterion code 0–7; default from config, otherwise 0 (Balance max)')
    p.add_argument('--symbol', help='Exact chart symbol for all files, including suffix')
    p.add_argument('--period', help='Chart timeframe for all files, e.g. H1, D1 or Daily')
    p.add_argument('--detect', choices=['ubs', 'filename', 'none'], default='ubs',
                   help='Detection for fields not supplied explicitly; unresolved fields are errors')
    p.add_argument('--symbol-chars', type=int, default=6, help='Leading filename characters used only with --detect filename')
    p.add_argument('--include', default='*.set', help='Case-insensitive filename glob when scanning a folder')
    p.add_argument('--no-recursive', action='store_true')
    p.add_argument('--skip-existing', action='store_true', help='Skip only valid reports matching input and settings fingerprints')
    p.add_argument('--timeout-hours', type=float, default=24, help='Maximum runtime per set')
    p.add_argument('--stop-file', type=Path, help='Stop after the current set when this file exists; does not delete the file')
    p.add_argument('--output-dir', type=Path, help='Run log and summary folder; default batch_runs/<unique run id>')
    p.add_argument('--dry-run', action='store_true', help='Validate and print a JSON plan without launching MT5 or writing files')
    return p


def period_name(value):
    name = str(value).upper()
    aliases = {'D': 'Daily', 'D1': 'Daily', 'DAILY': 'Daily', 'W1': 'Weekly',
               'WEEKLY': 'Weekly', 'MN': 'Monthly', 'MN1': 'Monthly', 'MONTHLY': 'Monthly'}
    name = aliases.get(name, name)
    if name not in runner.PERIODS:
        raise ValueError(f'Unsupported timeframe: {value}')
    return name


def prepare(args):
    if args.config.exists():
        saved = json.loads(args.config.read_text(encoding='utf-8-sig'))
        if not isinstance(saved, dict):
            raise ValueError('Config must contain a JSON object.')
    elif args.config.resolve() != (ROOT / 'mt5_batch_config.json').resolve():
        raise ValueError(f'Config does not exist: {args.config}')
    else:
        saved = {}
    cfg = dict(DEFAULTS)
    # Only supported settings enter the plan/log; unrelated configuration stays private.
    for key in list(DEFAULTS) + ['optimization_criterion']:
        if key in saved:
            cfg[key] = str(saved[key])
        override = getattr(args, key, None)
        if override is not None:
            cfg[key] = override
    cfg['model'] = MODELS[args.model] if args.model else MODEL_MIGRATE.get(cfg['model'], cfg['model'])
    if cfg['model'] not in MODELS.values():
        raise ValueError('Model must be 0, 1, 2 or 4.')
    cfg['optimization'] = ('1' if args.method == 'complete' else '2') if args.method else cfg['optimization']
    if args.mode == 'backtest':
        if args.method or args.criterion:
            raise ValueError('--method and --criterion apply only to optimize.')
        cfg['optimization'] = '0'
    elif cfg['optimization'] not in runner.METHODS:
        raise ValueError('Optimization method is required: use --method complete or --method genetic.')
    cfg['optimization_criterion'] = args.criterion or cfg.get('optimization_criterion', '0')
    for key in ('from_date', 'to_date'):
        cfg[key] = datetime.strptime(cfg[key], '%Y.%m.%d').strftime('%Y.%m.%d')
    if cfg['to_date'] <= cfg['from_date']:
        raise ValueError('To date must be after From date.')
    if not math.isfinite(float(cfg['deposit'])) or float(cfg['deposit']) <= 0 or int(cfg['leverage']) <= 0:
        raise ValueError('Deposit and leverage must be positive.')
    if not cfg['currency'].strip() or not cfg['ea_name'].strip():
        raise ValueError('Currency and EA are required.')
    if not math.isfinite(args.timeout_hours) or args.timeout_hours <= 0:
        raise ValueError('Timeout must be a positive number of hours.')
    for key in ('terminal_path', 'tester_folder'):
        if not cfg[key].strip():
            raise ValueError(f'{key} is required.')
        cfg[key] = str(Path(cfg[key]).expanduser().resolve())
    if not Path(cfg['terminal_path']).is_file():
        raise ValueError(f'Terminal executable not found: {cfg["terminal_path"]}')
    data = Path(cfg['tester_folder']).parent
    if not (data / 'MQL5').is_dir():
        raise ValueError('Tester folder must be inside an MT5 data folder containing MQL5.')
    ea = Path(cfg['ea_name'].replace('\\', '/'))
    if ea.is_absolute() or '..' in ea.parts:
        raise ValueError('EA must be a relative path inside MQL5/Experts.')
    if ea.suffix.lower() != '.ex5':
        ea = ea.with_suffix('.ex5')
    if not (data / 'MQL5' / 'Experts' / ea).is_file():
        raise ValueError(f'EA not found: {data / "MQL5" / "Experts" / ea}')
    cfg['ea_name'] = str(ea)
    if args.jobs:
        records = json.loads(args.jobs.read_text(encoding='utf-8-sig'))
        if not isinstance(records, list):
            raise ValueError('--jobs must contain a JSON list.')
        jobs = []
        for row in records:
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k].strip()
                                                   for k in ('path', 'symbol', 'period')):
                raise ValueError('Each job must have nonempty path, symbol and period strings.')
            path = Path(row['path'])
            if not path.is_absolute():
                path = args.jobs.resolve().parent / path
            jobs.append({'path': str(path.resolve()), 'symbol': row['symbol'], 'period': period_name(row['period'])})
    else:
        if not args.folder.is_dir():
            raise ValueError(f'Set folder not found: {args.folder}')
        jobs = []
        if args.symbol_chars < 1:
            raise ValueError('--symbol-chars must be positive.')
        for path in runner.discover_sets(args.folder, not args.no_recursive):
            if not fnmatch.fnmatch(Path(path).name.lower(), args.include.lower()):
                continue
            symbol, period = args.symbol, args.period
            if args.detect == 'ubs':
                symbol = symbol or get_ubs_symbol(path)
                period = period or get_ubs_period(path) or detect_timeframe(Path(path).name)
            elif args.detect == 'filename':
                symbol = symbol or Path(path).stem[:args.symbol_chars].upper() + cfg['suffix']
                period = period or detect_timeframe(Path(path).name)
            if not symbol or not period:
                raise ValueError(f'Cannot resolve symbol/timeframe for {path}; use --symbol and --period or --jobs.')
            jobs.append({'path': path, 'symbol': symbol, 'period': period_name(period)})
    if not jobs:
        raise ValueError('No set files selected.')
    seen = set()
    for job in jobs:
        if args.jobs:
            job['symbol'] = args.symbol or job['symbol']
            job['period'] = period_name(args.period or job['period'])
        path = Path(job['path'])
        if not path.is_file() or path.suffix.lower() != '.set':
            raise ValueError(f'Set file not found: {path}')
        if job['path'] in seen:
            raise ValueError(f'Duplicate set file: {path}')
        seen.add(job['path'])
        runner.build_ini(job, cfg, 'preview.set', 'preview', args.mode)
    return cfg, jobs


@contextmanager
def terminal_lock(path):
    """OS-released lock prevents simultaneous CLI batches for this executable."""
    import msvcrt
    key = hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / f'mt5tools_{key}.lock'
    with lock_path.open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ValueError('Another CLI batch is using this terminal.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def print_event(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def execute(args, cfg, jobs):
    if os.name != 'nt':
        raise ValueError('Live runs require Windows and MT5.')
    with terminal_lock(cfg['terminal_path']):
        if runner.terminal_running(cfg['terminal_path']):
            raise ValueError('The selected MT5 terminal is open. Close it before starting.')
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
        output = (args.output_dir or ROOT / 'batch_runs' / run_id).resolve()
        # Unique directory prevents overwriting a previous agent's run evidence.
        output.mkdir(parents=True, exist_ok=False)
        log = (output / 'events.jsonl').open('w', encoding='utf-8')
        stop, events = threading.Event(), queue.Queue()
        results, errors = {}, []
        def emit(event):
            event = dict(event, timestamp=datetime.now(timezone.utc).isoformat())
            log.write(json.dumps(event, ensure_ascii=True) + '\n')
            log.flush()
            print_event(event)
        def request_stop(signum, frame):
            stop.set()
        previous = signal.signal(signal.SIGINT, request_stop)
        thread = threading.Thread(target=runner.run_batch,
            args=(jobs, cfg, events, stop, args.skip_existing, args.timeout_hours, args.mode))
        try:
            emit({'status': 'started', 'mode': args.mode, 'total': len(jobs), 'output_dir': str(output), 'config': cfg, 'jobs': jobs})
            if args.stop_file and args.stop_file.exists():
                stop.set()
            thread.start()
            announced_stop = False
            while thread.is_alive() or not events.empty():
                if args.stop_file and args.stop_file.exists():
                    stop.set()
                if stop.is_set() and not announced_stop:
                    emit({'status': 'stop_requested', 'message': 'Finishing current set before stopping.'})
                    announced_stop = True
                try:
                    event = events.get(timeout=0.25)
                except queue.Empty:
                    continue
                if 'idx' in event:
                    results[event['idx']] = event
                if event['status'] == 'error':
                    errors.append(event['message'])
                emit(event)
            counts = {status: sum(r['status'] == status for r in results.values())
                      for status in ('done', 'skipped', 'failed')}
            remaining = len(jobs) - sum(counts.values())
            exit_code = 130 if stop.is_set() else (1 if errors or counts['failed'] or remaining else 0)
            summary = {'status': 'summary', 'mode': args.mode, 'exit_code': exit_code,
                       'total': len(jobs), **counts, 'not_completed': remaining, 'errors': errors,
                       'results': [results[k] for k in sorted(results)], 'output_dir': str(output)}
            (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
            emit(summary)
            return exit_code
        finally:
            stop.set()
            # If stdout/log writing fails, allow the current set to finish; do not
            # leave an unattended queue launching additional tests after CLI exit.
            if thread.ident is not None:
                thread.join()
            signal.signal(signal.SIGINT, previous)
            log.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        cfg, jobs = prepare(args)
        if args.dry_run:
            print_event({'status': 'dry_run', 'mode': args.mode, 'config': cfg, 'total': len(jobs),
                         'jobs': [dict(job, report=str(runner.report_path(job, cfg, args.mode)),
                                       optimized_inputs=runner.optimized_inputs(job['path'])) for job in jobs]})
            return 0
        return execute(args, cfg, jobs)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        print_event({'status': 'error', 'exit_code': 2, 'message': str(exc)})
        return 2


if __name__ == '__main__':
    sys.exit(main())
