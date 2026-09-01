import logging
import tempfile
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
