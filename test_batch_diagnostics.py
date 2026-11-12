"""Diagnostics tests use temporary files and mocks, never a real terminal."""
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from batch_diagnostics import SessionLog, JournalWatch, failure_summaries


class DiagnosticsTests(unittest.TestCase):
    def test_failure_summary_separates_tests_and_ignores_recovered_errors(self):
        def line(level, message):
            return f'2026-11-12T00:00:00+00:00 [{level}] {message}'
        lines = [
            line('INFO', 'Preparing C:\\sets\\first.set: symbol=BAD; timeframe=H1'),
            line('ERROR', 'MT5: unknown symbol BAD'),
            line('ERROR', 'failed: first.set No report found'),
            line('INFO', 'Preparing second.set: symbol=EURUSD; timeframe=H1'),
            line('WARNING', 'Account login not confirmed'),
            line('ERROR', 'No report produced. Review journal errors above for authorization, symbol/history, EA loading or terminal/data-folder issues.'),
            line('ERROR', 'failed: second.set No report found'),
            line('INFO', 'Preparing recovered.set: symbol=EURUSD; timeframe=H1'),
            line('ERROR', 'MT5: no connection'),
            line('INFO', 'done: recovered.set Report saved'),
        ]
        summaries = failure_summaries(lines)
        self.assertEqual(len(summaries), 2)
        self.assertIn('requested instrument', summaries[0]['findings'][0][0])
        self.assertIn('does not establish', summaries[1]['findings'][0][0])
        self.assertNotIn('login problem', str(summaries))

    def test_failure_summary_timeout_and_setup(self):
        summaries = failure_summaries([
            'now [ERROR] error: Batch could not start: PermissionError: access denied',
        ])
        self.assertEqual(summaries[0]['file'], 'Batch setup')
        self.assertIn('denied', summaries[0]['findings'][0][0])
        summaries = failure_summaries([
            'now [INFO] Preparing one.set: symbol=EURUSD; timeframe=H1',
            'now [ERROR] Time limit reached; terminating the owned terminal.',
            'now [ERROR] failed: one.set Optimization exceeded the configured time limit.',
        ])
        self.assertEqual(len(summaries[0]['findings']), 1)
        self.assertIn('time limit', summaries[0]['findings'][0][0])

    def test_default_errors_and_masking(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionLog(root=tmp)
            log.write('DEBUG', 'hidden detail')
            log.write('ERROR', 'password=abcdef account 12345678 failed')
            text = log.path.read_text()
            self.assertNotIn('hidden detail', text)
            self.assertNotIn('abcdef', text)
            self.assertNotIn('12345678', text)
            self.assertIn('failed', text)
            self.assertNotEqual(log.path, SessionLog(root=tmp).path)

    def test_journal_only_new_evidence_and_partial_utf16(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = root / 'Logs' / 'today.log'
            journal.parent.mkdir()
            journal.write_text('old authorized\n', encoding='utf-16')
            log = SessionLog(verbose=True, root=root / 'sessions')
            watch = JournalWatch(root, log, 'EURUSD.a')
            watch.poll()
            self.assertFalse(watch.login_seen)
            with journal.open('ab') as out:
                out.write('EURUSD.ab synchronized\nnot authorized\nEURUSD.a synchro'.encode('utf-16-le'))
            watch.poll()
            self.assertFalse(watch.login_seen)
            self.assertFalse(watch.symbol_seen)
            with journal.open('ab') as out:
                out.write('nized\naccount 12345678 authorized\n'.encode('utf-16-le'))
            watch.finish()
            self.assertTrue(watch.login_seen)
            self.assertTrue(watch.symbol_seen)
            text = log.path.read_text()
            self.assertNotIn('old authorized', text)
            self.assertIn('[ERROR]', text)
            self.assertNotIn('12345678', text)

    def test_new_journal_errors_without_verbose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = SessionLog(root=root / 'sessions')
            watch = JournalWatch(root, log, 'BTCUSD')
            journal = root / 'Tester' / 'Agent-local' / 'logs' / 'today.log'
            journal.parent.mkdir(parents=True)
            journal.write_text('normal detail\nunknown symbol BTCUSD\n')
            watch.finish()
            text = log.path.read_text()
            self.assertIn('unknown symbol BTCUSD', text)
            self.assertNotIn('normal detail', text)
            self.assertIn('not confirmed', text)

    def test_runner_setup_failure_always_completes(self):
        import view_batch_backtest as view
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionLog(root=tmp)
            events = queue.Queue()
            with patch.object(view, 'SessionLog', return_value=log), patch.object(view, '_run_batch', side_effect=ValueError('bad settings')):
                view.run_batch([], tmp, {}, tmp, 'asis', [], [], [], '', events)
            self.assertEqual(events.get_nowait()['status'], 'error')
            self.assertEqual(events.get_nowait()['status'], 'complete')
            self.assertIn('ValueError: bad settings', log.path.read_text())

    def test_runner_logs_events(self):
        import view_batch_backtest as view
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionLog(root=tmp)
            events = queue.Queue()
            def fake_run(*args):
                args[9].put({'status': 'done', 'file': 'test.set', 'report': 'test.htm'})
            with patch.object(view, 'SessionLog', return_value=log), patch.object(view, '_run_batch', side_effect=fake_run):
                view.run_batch([], tmp, {}, tmp, 'asis', [], [], [], '', events)
            self.assertEqual(events.get_nowait()['status'], 'done')
            self.assertEqual(events.get_nowait()['status'], 'complete')
            self.assertIn('test.htm', log.path.read_text())

    def test_log_viewer(self):
        import view_batch_backtest as view
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionLog(root=Path(tmp) / 'batch_runs')
            log.write('ERROR', 'Example diagnostic')
            with patch.object(view, '__file__', str(Path(tmp) / 'view_batch_backtest.py')):
                page = AppTest.from_string('from view_batch_backtest import render_session_logs\nrender_session_logs()').run(timeout=30)
            self.assertFalse(page.exception)
            self.assertIn('Example diagnostic', page.code[0].value)


if __name__ == '__main__':
    unittest.main()
