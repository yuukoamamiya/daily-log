import tempfile
import unittest
from pathlib import Path

from daily_log.database import DailyLogDatabase
from daily_log.paths import AppPaths
from daily_log.projection import project_pending
from daily_log.runtime import bootstrap_runtime, migrate_legacy_runtime


class RuntimeBootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_profile_starts_empty_outside_program_directory(self):
        program = self.root / "readonly-program"
        program.mkdir()
        paths = AppPaths(self.root / "profile")
        database = DailyLogDatabase(paths.database)
        details = bootstrap_runtime(database, paths)
        self.assertEqual(details["mode"], "empty")
        self.assertTrue(database.is_initialized())
        self.assertEqual(database.list_transactions(), [])
        self.assertTrue((paths.portable_root / "data/todo/todo.txt").is_file())
        self.assertEqual(list(program.rglob("*")), [])

    def test_legacy_data_and_budget_migrate_once_then_only_profile_changes(self):
        legacy = self.root / "legacy"
        (legacy / "data/journal").mkdir(parents=True)
        (legacy / "data/diary").mkdir(parents=True)
        (legacy / "data/todo").mkdir(parents=True)
        (legacy / "data/calendar").mkdir(parents=True)
        source_ledger = legacy / "data/journal/2026.journal"
        source_ledger.write_text(
            "2026-08-01 午饭\n    (expenses:饮食)    12.00\n", encoding="utf-8"
        )
        (legacy / "data/journal/ledger.journal").write_text("include 2026.journal\n", encoding="utf-8")
        (legacy / "data/journal/budget.journal").write_text(
            "~ monthly  月度总预算\n    (expenses)    4567.00\n", encoding="utf-8"
        )
        (legacy / "data/diary/journal.txt").write_text("", encoding="utf-8")
        (legacy / "data/todo/todo.txt").write_text("", encoding="utf-8")
        (legacy / "data/todo/done.txt").write_text("", encoding="utf-8")
        original = source_ledger.read_text(encoding="utf-8")

        paths = AppPaths(self.root / "profile")
        database = DailyLogDatabase(paths.database)
        details = migrate_legacy_runtime(database, paths, legacy)
        self.assertEqual(details["mode"], "legacy")
        self.assertEqual(database.list_transactions()[0]["summary"], "午饭")
        self.assertEqual(database.get_monthly_budget(), 4567.0)

        database.apply_plan({
            "transactions": [{"date": "2026-08-02", "summary": "饮料", "amount": "4", "account": "expenses:饮食"}],
            "journal": [], "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-02")
        project_pending(database, paths.portable_root)
        self.assertEqual(source_ledger.read_text(encoding="utf-8"), original)
        self.assertIn("饮料", (paths.portable_root / "data/journal/2026.journal").read_text(encoding="utf-8"))
        self.assertEqual(bootstrap_runtime(database, paths)["mode"], "existing")

    def test_program_data_is_never_imported_without_explicit_migration(self):
        program = self.root / "program-with-personal-data"
        (program / "data/journal").mkdir(parents=True)
        (program / "data/journal/2026.journal").write_text(
            "2026-08-31 私人消费\n    (expenses:饮食)    22.00\n",
            encoding="utf-8",
        )
        paths = AppPaths(self.root / "new-user")
        database = DailyLogDatabase(paths.database)

        details = bootstrap_runtime(database, paths)

        self.assertEqual(details["mode"], "empty")
        self.assertEqual(database.list_transactions(), [])

    def test_explicit_migration_rejects_an_initialized_profile(self):
        legacy = self.root / "legacy"
        (legacy / "data/journal").mkdir(parents=True)
        (legacy / "data/journal/2026.journal").write_text(
            "2026-08-31 午饭\n    (expenses:饮食)    12.00\n",
            encoding="utf-8",
        )
        paths = AppPaths(self.root / "profile")
        database = DailyLogDatabase(paths.database)
        bootstrap_runtime(database, paths)

        with self.assertRaisesRegex(ValueError, "已经初始化"):
            migrate_legacy_runtime(database, paths, legacy)


if __name__ == "__main__":
    unittest.main()
