"""Never close a real terminal in tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import terminal_control as control


class TerminalControlTests(unittest.TestCase):
    def test_target_is_data_and_closed_pid_is_logged(self):
        messages = []
        with patch.object(control.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='[4321]', stderr='')) as run:
            control.close_selected_terminal(r'C:\MT5 test\terminal64.exe',
                                            lambda level, text: messages.append(text))
        self.assertIn('4321', ' '.join(messages))
        self.assertTrue(run.call_args.kwargs['env']['MT5_BATCH_CLOSE_TARGET'].endswith('terminal64.exe'))
        script = run.call_args.args[0][-1]
        self.assertIn('CloseMainWindow', script)
        self.assertIn('Get-Process -Id $candidate.Id', script)
        self.assertNotIn('Stop-Process', script)

    def test_refusal_is_reported(self):
        with patch.object(control.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='', stderr='Terminal PID 4321 did not close within 15 seconds')):
            with self.assertRaisesRegex(RuntimeError, 'Could not close selected terminal'):
                control.close_selected_terminal(r'C:\MT5\terminal64.exe', lambda *args: None)

    def test_no_terminal_needs_no_close(self):
        messages = []
        with patch.object(control.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='[]', stderr='')):
            control.close_selected_terminal(r'C:\MT5\terminal64.exe',
                                            lambda level, text: messages.append(text))
        self.assertIn('No existing', messages[-1])
