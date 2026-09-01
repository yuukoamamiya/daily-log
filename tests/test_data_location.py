import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_log.data_location import (
    read_redirect,
    relocate_profile,
    validate_relocation_target,
    write_redirect,
)
from daily_log.database import DailyLogDatabase, default_state_dir
from daily_log.errors import ValidationError
from daily_log.paths import AppPaths


class DataLocationTest(unittest.TestCase):
    def test_profile_is_copied_and_database_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            paths = AppPaths(source)
            paths.ensure()
            database = DailyLogDatabase(paths.database)
            database.initialize_empty()
            paths.config.write_text("[application]\nonboarding_completed = true\n", encoding="utf-8")
            (paths.portable_root / "data" / "diary").mkdir(parents=True)
            (paths.portable_root / "data" / "diary" / "journal.txt").write_text("保留投影", encoding="utf-8")

            result = relocate_profile(database, source, target, program_root=root / "program")

            self.assertEqual(result["path"], str(target.resolve()))
            self.assertTrue((target / "config.ini").is_file())
            self.assertTrue((target / "portable" / "data" / "diary" / "journal.txt").is_file())
            self.assertTrue(DailyLogDatabase(target / "daily-log.db").is_initialized())

    def test_relocation_rejects_non_empty_and_recursive_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            non_empty = root / "target"
            non_empty.mkdir()
            (non_empty / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "必须为空"):
                validate_relocation_target(non_empty, source, program_root=root / "program")
            with self.assertRaisesRegex(ValidationError, "当前数据目录内"):
                validate_relocation_target(source / "child", source, program_root=root / "program")

    def test_default_profile_redirect_is_one_hop_and_env_override_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_app_data = root / "local"
            target = root / "profile"
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local_app_data), "XDG_STATE_HOME": str(local_app_data)},
                clear=False,
            ):
                write_redirect(target)
                self.assertEqual(read_redirect(), target.resolve())
                self.assertEqual(default_state_dir(), target.resolve())
                with patch.dict(os.environ, {"DAILY_LOG_STATE_DIR": str(root / "explicit")}, clear=False):
                    self.assertEqual(default_state_dir(), (root / "explicit").resolve())
            payload = json.loads((local_app_data / "DailyLog" / ".profile-location.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["format"], 1)


if __name__ == "__main__":
    unittest.main()
