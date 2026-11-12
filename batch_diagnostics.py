"""Per-run diagnostics. Reads journal additions only; never connects to MT5."""
from pathlib import Path
from datetime import datetime, timezone
import re
import uuid


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
