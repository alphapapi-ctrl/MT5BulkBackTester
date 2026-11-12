"""Per-run diagnostics. Reads journal additions only; never connects to MT5."""
from pathlib import Path
from datetime import datetime, timezone
import re
import uuid


# Match explicit errors, never missing confirmation or generic troubleshooting text.
FAILURE_RULES = [
    (r'authorization failed|authentication failed|invalid (?:account|password)|not authorized',
     'MT5 reported a login problem.',
     'Check the account number, password and broker server in the selected terminal.'),
    (r'unknown symbol|invalid symbol|symbol.*not found|symbol.*not available',
     'MT5 could not use the requested instrument.',
     'Check the exact broker symbol, including its suffix, and whether this account offers it.'),
    (r'no history|not enough history|history.*(?:failed|unavailable)|no (?:price|tick) data',
     'MT5 reported missing or unavailable price history.',
     'Check the requested dates and test model, and download history for that symbol in MT5.'),
    (r'(?:cannot|failed to) load.*(?:expert|\.ex5)|expert.*(?:not found|loading failed)|initialization failed|oninit.*failed',
     'The EA could not load or initialise.',
     'Check the selected EA, its inputs, required files and any licence restrictions in the tester journal.'),
    (r'terminal executable missing|tester folder missing|check the terminal executable',
     'The configured terminal or data folder could not be found.',
     'Check the terminal executable and its matching Tester folder in Active Config.'),
    (r'terminal is already open',
     'The selected terminal was already open, so this test did not start.',
     'Close that terminal before retrying the batch.'),
    (r'time limit reached|exceeded the configured time limit',
     'The test exceeded its configured time limit.',
     'Increase the per-set time limit or reduce the optimisation range after checking that MT5 is making progress.'),
    (r'permissionerror|permission denied|access is denied|access denied',
     'Access to a required file or process was denied.',
     'Check folder permissions and whether another program has locked the file.'),
    (r'no enabled optimization inputs',
     'The set has no parameters enabled for optimisation.',
     'Enable the intended optimisation inputs and ranges in MT5, then save the set again.'),
    (r'no connection|connection failed|connection.*lost',
     'MT5 reported a connection problem.',
     'Check internet connectivity and the broker server connection in the selected terminal.'),
    (r'mt5 exited with code|terminal exited: code=(?!0\b)-?\d+',
     'The terminal exited with an error code.',
     'Review the nearby tester journal entries for the underlying error before retrying.'),
]


def failure_summaries(lines):
    """Read an entire session incrementally; keep each test's evidence separate."""
    summaries = []
    current = None

    def finish():
        if current is not None and current['failed']:
            if not current['findings']:
                current['findings'].append((
                    'This test failed, but the captured log does not establish the cause.',
                    'Check the error details below, the terminal/data-folder pairing, EA settings and report location. Enable verbose logging for a retry if more detail is needed.'))
            summaries.append(current)

    for raw in lines:
        match = re.match(r'^\S+ \[(\w+)\] (.*)$', raw.rstrip())
        if not match:
            continue
        level, message = match.groups()
        if message.startswith('Preparing '):
            finish()
            name = message[len('Preparing '):].split(': symbol=', 1)[0]
            current = {'file': name, 'failed': False, 'findings': [], 'evidence': []}
        if level != 'ERROR':
            continue
        # Queue outcome is the authority: journal errors can be recovered from.
        outcome = re.match(r'^(failed|error): ', message)
        if current is None and outcome:
            current = {'file': 'Batch setup', 'failed': False, 'findings': [], 'evidence': []}
        if current is None:
            continue
        if outcome:
            current['failed'] = True
        if message.startswith('No report produced. Review journal errors above'):
            continue  # This is our generic checklist, not evidence of a cause.
        for pattern, explanation, checks in FAILURE_RULES:
            if re.search(pattern, message, re.I):
                finding = (explanation, checks)
                if finding not in current['findings']:
                    current['findings'].append(finding)
                    current['evidence'].append(message[:600])
        if outcome and not current['evidence']:
            current['evidence'].append(message[:600])
    finish()
    return summaries


def redact(value):
    text = str(value)
    text = re.sub(r"(?i)((?:password|passwd|token|secret)\s*[=:]\s*)\S+", r"\1[redacted]", text)
    text = re.sub(r"(?<!\d)\d{6,}(?!\d)", "[number redacted]", text)
    return text


class SessionLog:
    def __init__(self, verbose=False, root=None):
        root = Path(root) if root else Path(__file__).resolve().parent / 'batch_runs'
        self.folder = root / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
        self.folder.mkdir(parents=True, exist_ok=False)
        self.path = self.folder / 'session.log'
        self.verbose = verbose
        self.write('INFO', 'Session started; times are UTC. Login/symbol status comes only from new journal evidence.')

    def write(self, level, message):
        if level == 'DEBUG' and not self.verbose:
            return
        timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.path.open('a', encoding='utf-8') as output:
            for line in redact(message).splitlines():
                output.write(f'{timestamp} [{level}] {line}\n')


class JournalWatch:
    """Tail terminal, tester and local-agent journals without replaying old content."""
    def __init__(self, data_folder, log, symbol):
        self.data = Path(data_folder)
        self.log = log
        self.symbol = symbol
        self.positions = {}
        self.pending = {}
        self.encodings = {}
        self.login_seen = False
        self.symbol_seen = False
        self.lines_seen = 0
        for path in self.files():
            try:
                self.positions[path] = path.stat().st_size
            except OSError:
                pass
        log.write('INFO', f'Journal monitoring started for {symbol}. Account login and broker symbol availability are not yet confirmed.')

    def files(self):
        found = []
        for pattern in ('Logs/*.log', 'Tester/logs/*.log', 'Tester/Agent-*/logs/*.log'):
            found.extend(self.data.glob(pattern))
        return found

    def poll(self, final=False):
        for path in self.files():
            try:
                size = path.stat().st_size
                start = self.positions.get(path, 0)
                if size < start:
                    start = 0
                    self.pending.pop(path, None)
                if size == start:
                    continue
                with path.open('rb') as source:
                    bom = source.read(2)
                    encoding = 'utf-16-le' if bom == b'\xff\xfe' else ('utf-16-be' if bom == b'\xfe\xff' else 'utf-8')
                    source.seek(start)
                    raw = self.pending.pop(path, b'') + source.read(-1 if final else 256 * 1024)
                    self.positions[path] = source.tell()
                self.encodings[path] = encoding
                delimiter = '\n'.encode(encoding)
                chunks = raw.split(delimiter)
                self.pending[path] = chunks.pop()
                for chunk in chunks:
                    self.consume(path, chunk.decode(encoding, errors='replace').lstrip('\ufeff').strip())
            except OSError as exc:
                self.log.write('WARNING', f'Journal temporarily unreadable: {path.name}: {exc}')
        if final:
            for path, raw in list(self.pending.items()):
                if raw:
                    self.consume(path, raw.decode(self.encodings.get(path, 'utf-8'), errors='replace').strip())
            self.pending.clear()

    def consume(self, path, line):
        if not line:
            return
        self.lines_seen += 1
        lower = line.lower()
        failure = any(word in lower for word in ('error', 'failed', 'cannot', 'not found', 'invalid', 'no connection', 'not available', 'unknown symbol', 'no history', 'not authorized', 'not synchronized'))
        self.log.write('ERROR' if failure else 'DEBUG', f'MT5 [{path.relative_to(self.data)}] {line}')
        if not failure and re.search(r'\bauthorized\b|\blogin successful\b|\blogged in\b', lower) and not self.login_seen:
            self.login_seen = True
            self.log.write('INFO', f'Account login reported by journal: {line}')
        symbol_match = re.search(r'(?<![\w.])' + re.escape(self.symbol) + r'(?![\w.])', line, re.I)
        if not failure and symbol_match and re.search(r'\bsynchronized\b|\bsynchronization completed\b', lower) and not self.symbol_seen:
            self.symbol_seen = True
            self.log.write('INFO', f'Symbol data synchronization reported for {self.symbol}: {line}')

    def finish(self):
        self.poll(final=True)
        if not self.login_seen:
            self.log.write('WARNING', 'Account login not confirmed in captured journal lines (not necessarily a failure).')
        if not self.symbol_seen:
            self.log.write('WARNING', f'Broker availability/data synchronization for {self.symbol} not confirmed in captured journal lines.')
        if not self.lines_seen:
            self.log.write('WARNING', 'No new journal lines found. Check terminal/data-folder pairing or alternate agent journal locations.')
