import threading
import unittest
from unittest.mock import Mock, patch

from daily_log.desktop_app import DesktopApplication


class DesktopShellTest(unittest.TestCase):
    def make_application(self, *, pending=False, backup=None, idle_backup=None):
        window = Mock()
        application = DesktopApplication(
            webview=Mock(),
            pystray=Mock(),
            window=window,
            server=Mock(),
            instance=Mock(),
            flush=Mock(),
            backup_status=Mock(return_value={"pending": pending}),
            backup=backup,
            idle_backup=idle_backup,
        )
        return application, window

    def test_window_close_flushes_and_finishes_backup_before_destroy(self):
        backup = Mock()
        application, window = self.make_application(pending=True, backup=backup)

        self.assertFalse(application.on_closing(window))
        application._close_thread.join(timeout=2)

        window.hide.assert_called_once_with()
        backup.assert_called_once()
        self.assertEqual(backup.call_args.args, ("关闭前自动备份",))
        self.assertGreaterEqual(backup.call_args.kwargs["timeout"], 1)
        self.assertLessEqual(backup.call_args.kwargs["timeout"], 15)
        window.destroy.assert_called_once_with()
        self.assertTrue(application.allow_close)

    def test_close_without_pending_backup_only_flushes_locally(self):
        backup = Mock()
        application, window = self.make_application(pending=False, backup=backup)

        self.assertFalse(application.on_closing(window))

        backup.assert_not_called()
        window.destroy.assert_called_once_with()

    def test_show_window_cancels_close_while_backup_is_running(self):
        started = threading.Event()
        release = threading.Event()

        def backup(*_args, **_kwargs):
            started.set()
            release.wait(timeout=2)

        application, window = self.make_application(pending=True, backup=backup)
        self.assertFalse(application.on_closing(window))
        self.assertTrue(started.wait(timeout=2))

        application._show_window()
        release.set()
        application._close_thread.join(timeout=2)

        window.show.assert_called_once_with()
        window.destroy.assert_not_called()
        self.assertFalse(application.allow_close)

    def test_close_waits_for_an_existing_backup_without_starting_another(self):
        backup = Mock()
        status = Mock(side_effect=[{"pending": True, "busy": True}, {"pending": False, "busy": False}])
        application, window = self.make_application(pending=True, backup=backup)
        application._backup_status_callback = status

        application._begin_close()
        application._close_thread.join(timeout=2)

        backup.assert_not_called()
        window.destroy.assert_called_once_with()

    def test_minimize_to_tray_only_wakes_idle_backup(self):
        idle_backup = Mock()
        backup = Mock()
        application, window = self.make_application(
            pending=False, backup=backup, idle_backup=idle_backup
        )

        application._minimize_to_tray()

        window.hide.assert_called_once_with()
        idle_backup.notify.assert_called_once_with()
        backup.assert_not_called()

    def test_failed_close_backup_still_exits_and_keeps_close_non_blocking(self):
        backup = Mock(side_effect=RuntimeError("网络不可用"))
        application, window = self.make_application(pending=True, backup=backup)

        application._request_exit()
        application._close_thread.join(timeout=2)

        backup.assert_called_once()
        window.destroy.assert_called_once_with()
        self.assertTrue(application.allow_close)

    def test_repeated_close_requests_share_one_close_attempt(self):
        started = threading.Event()
        release = threading.Event()
        backup_calls = []

        def backup(*_args, **_kwargs):
            backup_calls.append(True)
            started.set()
            release.wait(timeout=2)

        application, window = self.make_application(pending=True, backup=backup)
        application._request_exit()
        self.assertTrue(started.wait(timeout=2))
        close_thread = application._close_thread
        application._request_exit()
        self.assertIs(application._close_thread, close_thread)
        release.set()
        close_thread.join(timeout=2)
        self.assertEqual(len(backup_calls), 1)
        window.destroy.assert_called_once_with()

    def test_explicit_exit_uses_the_same_close_flow(self):
        application, window = self.make_application(pending=False, backup=Mock())

        application._exit()

        window.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
