import unittest
from unittest.mock import Mock

from daily_log.desktop_app import DesktopApplication


class DesktopShellTest(unittest.TestCase):
    def test_window_close_hides_to_tray_until_exit_is_requested(self):
        window = Mock()
        application = DesktopApplication(
            webview=Mock(), pystray=Mock(), window=window, server=Mock(), instance=Mock()
        )
        self.assertFalse(application.on_closing(window))
        window.hide.assert_called_once_with()
        application.allow_close = True
        self.assertTrue(application.on_closing(window))


if __name__ == "__main__":
    unittest.main()
