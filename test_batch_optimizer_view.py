"""Smoke tests for the Streamlit page, without starting MT5."""
import tempfile
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest


class OptimizerViewTests(unittest.TestCase):
    def page(self):
        return AppTest.from_string('from view_batch_optimizer import render\nrender()').run(timeout=20)

    def test_page_loads(self):
        page = self.page()
        self.assertFalse(page.exception)
        self.assertEqual(page.title[0].value, 'Batch Optimizer')

    def test_nested_optimization_set_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'nested' / 'Optimization EURUSD H1.set'
            source.parent.mkdir()
            source.write_text('Lots=0.1||0.01||0.01||0.2||Y\nForceSymbol=EURUSD.a\n', encoding='utf-16')
            page = self.page()
            page.text_input(key='bo_folder').set_value(tmp).run(timeout=20)
            self.assertFalse(page.exception)
            self.assertTrue(page.dataframe)
            self.assertEqual(page.dataframe[0].value.iloc[0]['Symbol'], 'EURUSD.a')
            self.assertEqual(page.dataframe[0].value.iloc[0]['Enabled inputs'], 1)
            self.assertFalse(page.button(key='bo_start').disabled)


if __name__ == '__main__':
    unittest.main()
