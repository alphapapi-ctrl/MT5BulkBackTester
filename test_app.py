"""Standalone navigation smoke checks; no live terminal jobs."""
import unittest
from streamlit.testing.v1 import AppTest
from unittest.mock import patch


class AppTests(unittest.TestCase):
    def test_home_and_module_links(self):
        page = AppTest.from_file("app.py").run(timeout=30)
        self.assertFalse(page.exception)
        self.assertEqual(page.title[0].value, "MT5 Bulk Backtester")
        self.assertEqual(len(page.get("page_link")), 2)

    def test_backtest_first_run(self):
        with patch("view_batch_backtest.load_config", return_value=None):
            page = AppTest.from_string("from view_batch_backtest import render\nrender()").run(timeout=30)
        self.assertFalse(page.exception)
        self.assertTrue(any("First-Run" in item.value for item in page.subheader))


if __name__ == "__main__":
    unittest.main()
