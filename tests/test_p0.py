import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path

from daily_log.diagnostics import close_logging, configure_logging, redact_text
from daily_log.paths import AppPaths
from daily_log.single_instance import SingleInstance


class P0ReliabilityTest(unittest.TestCase):
    def test_single_instance_lock_rejects_second_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / ".instance.lock"
            first = SingleInstance(lock_path)
            second = SingleInstance(lock_path)
            first.acquire()
            try:
                with self.assertRaisesRegex(RuntimeError, "已经有 Daily Log"):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()

    def test_second_launch_can_request_existing_desktop_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / ".instance.lock"
            first = SingleInstance(lock_path)
            second = SingleInstance(lock_path)
            called = threading.Event()
            first.acquire()
            first.mark_reopen_available()
            first.start_reopen_listener(called.set, interval=0.01)
            try:
                with self.assertRaisesRegex(RuntimeError, "已经有 Daily Log"):
                    second.acquire()
                self.assertTrue(second.reopen_available)
                second.request_reopen()
                self.assertTrue(called.wait(timeout=1))
                deadline = time.monotonic() + 1
                while first._reopen_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(first._reopen_path.exists())
            finally:
                first.stop_reopen_listener()
                first.release()

    def test_log_redaction_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(Path(directory))
            paths.ensure()
            try:
                configure_logging(paths.logs)
                logger = logging.getLogger("daily_log.test")
                logger.info("Authorization: Bearer secret-token api_key=another-secret")
                content = (paths.logs / "daily-log.log").read_text(encoding="utf-8")
                self.assertNotIn("secret-token", content)
                self.assertNotIn("another-secret", content)
                self.assertIn("[已隐藏]", redact_text("Authorization: Bearer secret-token"))
            finally:
                close_logging()


if __name__ == "__main__":
    unittest.main()
