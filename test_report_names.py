import tempfile
import unittest
from pathlib import Path
from mt5_batch_backtest import DEFAULTS
from view_batch_backtest import safe_report_name, build_ini


class ReportNameTests(unittest.TestCase):
    def test_path_label_stays_a_filename(self):
        label = r'C:\Users\pc\Desktop\Updated set files\test GOLDTRADEPRO XAUUSD.a Daily OHLC'
        name = safe_report_name(label)
        self.assertNotRegex(name, r'[<>:"/\\|?*\s]')
        self.assertEqual(Path(name).name, name)
        self.assertEqual(safe_report_name('Gold XAUUSD.a H1 OHLC'), 'Gold_XAUUSD.a_H1_OHLC')
        self.assertLessEqual(len(safe_report_name('a' * 300)), 160)

    def test_ini_cannot_redirect_report_using_a_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'input.set'
            source.write_text('Lots=0.1')
            ini = Path(tmp) / 'test.ini'
            build_ini('XAUUSD.a', 'H1', str(source), str(ini),
                      r'C:\missing folder\report', DEFAULTS)
            report = next(line for line in ini.read_text().splitlines() if line.startswith('Report='))
            self.assertEqual(report, 'Report=C__missing_folder_report')
