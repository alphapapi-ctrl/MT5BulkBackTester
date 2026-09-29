"""End-to-end CLI tests with a fake terminal; never launch MT5."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mt5_batch_cli as cli


HTML = '<html><body><table><tr><td>Test result</td></tr></table><img src="TOKEN.png"></body></html>'
XML = '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table/></Worksheet></Workbook>'


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.terminal = self.root / 'terminal64.exe'
        self.terminal.touch()
        self.data = self.root / 'data'
        experts = self.data / 'MQL5' / 'Experts'
        experts.mkdir(parents=True)
        (experts / 'Test.ex5').touch()
        self.folder = self.root / 'sets'
        self.source = self.folder / 'nested' / 'Optimization EURUSD H1.set'
        self.source.parent.mkdir(parents=True)
        self.original = 'Lots=0.1||0.01||0.01||0.2||Y\nForceSymbol=EURUSD.a\nST1_Timeframe=16385\n'.encode('utf-16')
        self.source.write_bytes(self.original)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'terminal_path': str(self.terminal),
            'tester_folder': str(self.data / 'Tester'), 'ea_name': 'Test.ex5',
            'from_date': '2024.08.01', 'to_date': '2026.08.31', 'optimization': '0'}))
        self.args = ['--config', str(self.config), '--folder', str(self.folder)]

    def call(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(args)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        return code, events

    def launch(self, args, **kwargs):
        ini = Path(args[1].removeprefix('/config:')).read_text(encoding='utf-16')
        fields = dict(line.split('=', 1) for line in ini.splitlines() if '=' in line)
        staged = self.data / 'MQL5' / 'Profiles' / 'Tester' / fields['ExpertParameters']
        self.assertEqual(staged.read_bytes(), self.original)
        name = fields['Report']
        if fields['Optimization'] == '0':
            (self.data / (name + '.htm')).write_bytes(HTML.replace('TOKEN', name).encode('utf-16'))
            (self.data / (name + '.png')).write_bytes(b'chart')
        else:
            (self.data / (name + '.xml')).write_text(XML)
        class Process:
            returncode = 0
            def poll(self): return self.returncode
        return Process()

    def test_dry_run_recurses_and_does_not_launch_or_write(self):
        with patch.object(cli.runner.subprocess, 'Popen') as launch:
            before = set(self.root.rglob('*'))
            code, events = self.call(['backtest', *self.args, '--model', 'realticks', '--dry-run'])
        self.assertEqual(code, 0)
        launch.assert_not_called()
        self.assertEqual(before, set(self.root.rglob('*')))
        self.assertEqual(events[0]['config']['model'], '4')
        self.assertEqual(events[0]['jobs'][0]['symbol'], 'EURUSD.a')
        self.assertIn('REALTICKS', events[0]['jobs'][0]['report'])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_optimizer_method_required_and_config_not_changed(self):
        before = self.config.read_bytes()
        code, events = self.call(['optimize', *self.args, '--dry-run'])
        self.assertEqual(code, 2)
        self.assertIn('--method', events[-1]['message'])
        code, events = self.call(['optimize', *self.args, '--method', 'complete', '--criterion', '5', '--dry-run'])
        self.assertEqual(code, 0)
        self.assertEqual(events[0]['config']['optimization'], '1')
        self.assertEqual(events[0]['config']['optimization_criterion'], '5')
        self.assertEqual(self.config.read_bytes(), before)

    def test_explicit_symbol_period_and_relative_jobs(self):
        jobs = self.root / 'jobs.json'
        jobs.write_text(json.dumps([{'path': str(self.source.relative_to(self.root)), 'symbol': 'XAUUSD', 'period': 'D1'}]))
        code, events = self.call(['backtest', '--config', str(self.config), '--jobs', str(jobs), '--symbol', 'GoldDukascopy', '--dry-run'])
        self.assertEqual(code, 0)
        self.assertEqual(events[0]['jobs'][0]['symbol'], 'GoldDukascopy')
        self.assertEqual(events[0]['jobs'][0]['period'], 'Daily')

    def test_disabled_optimization_inputs_allowed_for_backtest_only(self):
        self.source.write_bytes(self.original.decode('utf-16').replace('||Y', '||N').encode('utf-16'))
        self.assertEqual(self.call(['backtest', *self.args, '--dry-run'])[0], 0)
        self.assertEqual(self.call(['optimize', *self.args, '--method', 'genetic', '--dry-run'])[0], 2)

    def test_bad_config_and_empty_selection_fail_before_launch(self):
        for flags in (['--to-date', '2020.01.01'], ['--deposit', 'nan'], ['--timeout-hours', '-1'],
                      ['--include', '*.missing'], ['--no-recursive'], ['--ea', 'Missing.ex5']):
            with self.subTest(flags=flags), patch.object(cli.runner.subprocess, 'Popen') as launch:
                code, _ = self.call(['backtest', *self.args, *flags, '--dry-run'])
                self.assertEqual(code, 2)
                launch.assert_not_called()

    def test_backtest_exports_charts_logs_and_summary_then_resumes(self):
        with patch.object(cli.runner, 'terminal_running', return_value=False), patch.object(cli.runner.subprocess, 'Popen', side_effect=self.launch) as launch:
            for i in range(2):
                output = self.root / f'run{i}'
                code, events = self.call(['backtest', *self.args, '--output-dir', str(output), '--skip-existing'])
                self.assertEqual(code, 0)
                summary = json.loads((output / 'summary.json').read_text())
                self.assertEqual(summary['done' if i == 0 else 'skipped'], 1)
                self.assertTrue((output / 'events.jsonl').is_file())
            self.assertEqual(launch.call_count, 1)
        report = Path(summary['results'][0]['report'])
        self.assertTrue(report.with_suffix('.png').is_file())
        self.assertIn(report.with_suffix('.png').name, report.read_text(encoding='utf-16'))
        self.assertFalse(list(self.data.glob('batch_opt_*')))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_optimizer_exports_xml(self):
        with patch.object(cli.runner, 'terminal_running', return_value=False), patch.object(cli.runner.subprocess, 'Popen', side_effect=self.launch):
            code, events = self.call(['optimize', *self.args, '--method', 'complete', '--output-dir', str(self.root / 'run')])
        self.assertEqual(code, 0)
        report = Path(events[-1]['results'][0]['report'])
        self.assertEqual(report.parent.name, 'OptimisationReport')
        self.assertTrue(cli.runner.valid_report(report))

    def test_failed_job_returns_one_and_processes_next_job(self):
        second = self.source.with_name('Second H1.set')
        second.write_bytes(self.original)
        count = 0
        def launch(args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                raise OSError('Simulated launch failure')
            return self.launch(args, **kwargs)
        with patch.object(cli.runner, 'terminal_running', return_value=False), patch.object(cli.runner.subprocess, 'Popen', side_effect=launch):
            code, events = self.call(['backtest', *self.args, '--output-dir', str(self.root / 'run')])
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]['failed'], 1)
        self.assertEqual(events[-1]['done'], 1)

    def test_stop_file_prevents_launch_and_returns_130(self):
        stop = self.root / 'stop'
        stop.touch()
        with patch.object(cli.runner, 'terminal_running', return_value=False), patch.object(cli.runner.subprocess, 'Popen') as launch:
            code, events = self.call(['backtest', *self.args, '--stop-file', str(stop), '--output-dir', str(self.root / 'run')])
        launch.assert_not_called()
        self.assertEqual(code, 130)
        self.assertEqual(events[-1]['not_completed'], 1)

    def test_busy_terminal_and_cli_lock(self):
        with patch.object(cli.runner, 'terminal_running', return_value=True):
            self.assertEqual(self.call(['backtest', *self.args])[0], 2)
        with cli.terminal_lock(self.terminal):
            with self.assertRaisesRegex(ValueError, 'Another CLI'):
                with cli.terminal_lock(self.terminal):
                    self.fail('Second lock must not acquire')
        with cli.terminal_lock(self.terminal):
            pass  # Released by the previous context.


if __name__ == '__main__':
    unittest.main()
