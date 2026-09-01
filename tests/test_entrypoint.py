import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StandaloneEntrypointTest(unittest.TestCase):
    def test_python_module_exposes_client_launcher(self):
        result = subprocess.run(
            [sys.executable, "-m", "daily_log", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--data-dir", result.stdout)
        self.assertIn("--migrate-from", result.stdout)


if __name__ == "__main__":
    unittest.main()
