"""Runner tests use a simulated MT5 process; no terminal is launched."""
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mt5_batch_optimizer as opt
from mt5_batch_backtest import DEFAULTS

XML = '<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table/></Worksheet></Workbook>'


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'terminal'
        (self.data / 'MQL5').mkdir(parents=True)
        self.exe = self.root / 'terminal64.exe'
        self.exe.touch()
        self.source = self.root / 'sets' / 'nested' / 'Optimization EURUSD H1.set'
        self.source.parent.mkdir(parents=True)
        self.original = ('; ranges must survive\r\nLots=0.1||0.01||0.01||0.2||Y\r\n'
                         'Fixed=10||4||1||20||N\r\n').encode('utf-16')
        self.source.write_bytes(self.original)
        self.job = {'path': str(self.source), 'symbol': 'EURUSD.a', 'period': 'H1'}
        self.cfg = dict(DEFAULTS, terminal_path=str(self.exe), tester_folder=str(self.data / 'Tester'),
                        optimization='2', optimization_criterion='6')

    def test_recursive_scan_includes_optimization_names_excludes_generated(self):
        for name in ('OptimisationReport', '_batch_modified', 'reports'):
            folder = self.source.parent / name
            folder.mkdir()
            (folder / 'copy.set').write_bytes(self.original)
        # Windows temporary paths can use 8.3 aliases (for example RUNNER~1).
        # Discovery returns resolved paths, so compare the same representation.
        self.assertEqual(opt.discover_sets(self.root / 'sets'), [str(self.source.resolve())])
        self.assertEqual(opt.discover_sets(self.root / 'sets', False), [])

    def test_ini_uses_optimization_and_preserves_source(self):
        ini = opt.build_ini(self.job, self.cfg, 'unique.set', 'unique')
        self.assertIn('Optimization=2\r\n', ini)
        self.assertIn('OptimizationCriterion=6\r\n', ini)
        self.assertIn('ExpertParameters=unique.set\r\n', ini)
        self.assertIn('UseCloud=0', ini)
        self.assertEqual(opt.optimized_inputs(self.source), ['Lots'])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_report_identity_and_location(self):
        original = opt.report_path(self.job, self.cfg)
        self.assertEqual(original.parent, self.source.parent / 'OptimisationReport')
        self.assertNotEqual(original, opt.report_path(self.job, dict(self.cfg, optimization='1')))
        self.assertNotEqual(original, opt.report_path(dict(self.job, symbol='GBPUSD'), self.cfg))
        self.source.write_bytes(self.original + 'Extra=1\r\n'.encode('utf-16-le'))
        self.assertNotEqual(original, opt.report_path(self.job, self.cfg))

    def run_fake(self, export=True, skip=False, stopped=False, timeout=False):
        events, stop = queue.Queue(), threading.Event()
        if stopped:
            stop.set()
        def launch(args, **kwargs):
            ini = Path(args[1].removeprefix('/config:')).read_text(encoding='utf-16')
            fields = dict(line.split('=', 1) for line in ini.splitlines() if '=' in line)
            staged = self.data / 'MQL5' / 'Profiles' / 'Tester' / fields['ExpertParameters']
            self.assertEqual(staged.read_bytes(), self.original)
            if export:
                (self.data / (fields['Report'] + '.xml')).write_text(XML)
            class Process:
                returncode = 0
                alive = timeout
                def poll(self): return None if self.alive else self.returncode
                def terminate(self): self.alive = False
                def wait(self, timeout=None): return self.returncode
            return Process()
        with patch.object(opt, 'terminal_running', return_value=False), patch.object(opt.subprocess, 'Popen', side_effect=launch) as popen:
            opt.run_batch([self.job], self.cfg, events, stop, skip, 0 if timeout else 1)
        result = []
        while not events.empty(): result.append(events.get_nowait())
        self.assertEqual(self.source.read_bytes(), self.original)
        return result, popen.call_count

    def test_runner_collects_xml_and_cleans_staged_files(self):
        events, calls = self.run_fake()
        self.assertEqual(calls, 1)
        self.assertEqual([e['status'] for e in events], ['running', 'done', 'complete'])
        self.assertTrue(opt.valid_report(opt.report_path(self.job, self.cfg)))
        self.assertEqual(list((self.data / 'MQL5' / 'Profiles' / 'Tester').iterdir()), [])

    def test_missing_report_is_failure(self):
        events, _ = self.run_fake(export=False)
        self.assertEqual(events[-2]['status'], 'failed')

    def test_skip_requires_valid_xml_and_matching_settings(self):
        destination = opt.report_path(self.job, self.cfg)
        destination.parent.mkdir()
        destination.write_text('broken')
        _, calls = self.run_fake(skip=True)
        self.assertEqual(calls, 1)
        events, calls = self.run_fake(skip=True)
        self.assertEqual(calls, 0)
        self.assertEqual(events[0]['status'], 'skipped')

    def test_stop_does_not_launch_next_set(self):
        events, calls = self.run_fake(stopped=True)
        self.assertEqual(calls, 0)
        self.assertTrue(events[-1]['stopped'])

    def test_timeout_reports_failure(self):
        events, _ = self.run_fake(export=False, timeout=True)
        self.assertIn('time limit', events[-2]['message'])

    def test_disabled_inputs_rejected(self):
        self.source.write_text('Lots=0.1||0.01||0.01||0.2||N')
        with self.assertRaisesRegex(ValueError, 'No enabled'):
            opt.validate_job(self.job, self.cfg)

    def test_terminal_check_handles_closed_matching_and_other_terminals(self):
        import json
        cases = [([], False), ([str(self.exe)], True),
                 ([str(self.root / 'other' / 'terminal64.exe')], False),
                 ([None], True)]
        for paths, expected in cases:
            with self.subTest(paths=paths), patch.object(opt.subprocess, 'run',
                    return_value=SimpleNamespace(returncode=0, stdout=json.dumps(paths), stderr='')):
                self.assertEqual(opt.terminal_running(str(self.exe)), expected)

    def test_terminal_check_reports_real_failure_details(self):
        with patch.object(opt.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='', stderr='Access denied')):
            with self.assertRaisesRegex(RuntimeError, 'Access denied'):
                opt.terminal_running(str(self.exe))

    def test_terminal_check_on_windows_with_no_mt5_running(self):
        # Run the actual PowerShell query, but filter an impossible process name
        # so this regression is safe even on a machine with a terminal open.
        actual_run = opt.subprocess.run
        def no_matches(args, **kwargs):
            args = list(args)
            args[-1] = args[-1].replace("-eq 'terminal64'", "-eq 'mt5_optimizer_nonexistent_test_process'")
            return actual_run(args, **kwargs)
        with patch.object(opt.subprocess, 'run', side_effect=no_matches):
            self.assertFalse(opt.terminal_running(str(self.exe)))


if __name__ == '__main__':
    unittest.main()
