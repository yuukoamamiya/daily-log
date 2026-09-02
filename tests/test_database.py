import tempfile
import unittest
import sqlite3
from pathlib import Path

from daily_log.database import DailyLogDatabase
from daily_log.errors import NotFoundError
from daily_log.projection import project_pending
from daily_log.storage import CalendarRepository, DiaryRepository, LedgerRepository, TodoRepository


class DatabaseProjectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for directory in ("data/journal", "data/diary", "data/todo", "data/calendar"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "data/journal/ledger.journal").write_text("include 2026.journal\n", encoding="utf-8")
        (self.root / "data/journal/2026.journal").write_text(
            "; 2026 年流水账\n\n2026-08-01 午饭\n    (expenses:饮食)    12.00\n", encoding="utf-8"
        )
        (self.root / "data/diary/journal.txt").write_text(
            "[2026-08-01 09:00:00 AM] 原日记 @生活\n", encoding="utf-8"
        )
        (self.root / "data/todo/todo.txt").write_text("2026-08-01 原待办 @生活\n", encoding="utf-8")
        (self.root / "data/todo/done.txt").write_text("", encoding="utf-8")
        self.database = DailyLogDatabase(self.root / "state/daily-log.db")
        self.assertTrue(self.database.import_text_data(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def test_import_is_one_time_and_keeps_existing_data(self):
        self.assertFalse(self.database.import_text_data(self.root))
        self.assertEqual(self.database.list_transactions()[0]["summary"], "午饭")
        self.assertEqual(self.database.list_diary()[0]["text"], "原日记")
        self.assertEqual(self.database.list_todos()[0]["text"], "原待办")
        self.assertIsNone(self.database.list_todos()[0]["dueDate"])

    def test_legacy_reminder_date_migrates_to_due_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE todos(id TEXT PRIMARY KEY, created_date TEXT NOT NULL, reminder_date TEXT, "
                    "text TEXT NOT NULL, tags_json TEXT NOT NULL DEFAULT '[]', completed INTEGER NOT NULL DEFAULT 0, "
                    "completed_date TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO todos VALUES(?,?,?,?,?,?,?,?,?)",
                    ("todo-old", "2026-08-01", "2026-08-09", "旧待办", "[]", 0, None, "now", "now"),
                )
                connection.commit()
            finally:
                connection.close()
            migrated = DailyLogDatabase(path)
            self.assertEqual(migrated.list_todos()[0]["dueDate"], "2026-08-09")

    def test_database_updates_immediately_then_projects_all_formats(self):
        plan = {
            "transactions": [{"date": "2026-08-02", "summary": "饮料", "amount": "4", "account": "expenses:饮食", "note": ""}],
            "journal": [{"date": "2026-08-02", "text": "新日记", "tags": ["生活"]}],
            "todos": [{"created_date": "2026-08-02", "due_date": "2026-08-05", "text": "新待办", "tags": ["工作"]}],
            "calendar": [{"date": "2026-08-03", "title": "开会", "start_time": "10:00", "end_time": "11:00", "location": "", "description": ""}],
            "clarifications": [],
        }
        self.database.apply_plan(plan, "2026-08-02")
        self.assertEqual(self.database.maintenance_status()["pending"], 4)
        self.assertEqual(self.database.list_transactions()[0]["summary"], "饮料")
        self.assertNotIn("饮料", (self.root / "data/journal/2026.journal").read_text(encoding="utf-8"))

        self.assertEqual(project_pending(self.database, self.root), 4)
        self.assertEqual(self.database.maintenance_status()["pending"], 0)
        self.assertTrue(any(item["summary"] == "饮料" for item in LedgerRepository(self.root).list()))
        self.assertTrue(any(item["text"] == "新日记" for item in DiaryRepository(self.root).list()))
        projected_todo = next(item for item in TodoRepository(self.root).list() if item["text"] == "新待办")
        self.assertEqual(projected_todo["dueDate"], "2026-08-05")
        self.assertIn("due:2026-08-05", projected_todo["rawText"])
        self.assertTrue(any(item["title"] == "开会" for item in CalendarRepository(self.root).list()))

    def test_edit_complete_restore_and_delete_keep_stable_database_id(self):
        todo = self.database.list_todos()[0]
        self.database.update("todo", todo["id"], {"created_date": todo["date"], "text": "修改待办", "tags": ["工作"]})
        self.database.complete_todo(todo["id"], "2026-08-03")
        self.assertEqual(self.database.list_todos(include_completed=True)[0]["id"], todo["id"])
        self.assertTrue(self.database.list_todos(include_completed=True)[0]["completed"])
        project_pending(self.database, self.root)
        completed = TodoRepository(self.root).list(include_completed=True)[0]
        self.assertTrue(completed["completed"])
        self.assertEqual(completed["text"], "修改待办")

        self.database.restore_todo(todo["id"])
        project_pending(self.database, self.root)
        self.assertFalse(TodoRepository(self.root).list(include_completed=True)[0]["completed"])
        self.database.delete("todo", todo["id"])
        project_pending(self.database, self.root)
        self.assertEqual(TodoRepository(self.root).list(include_completed=True), [])

    def test_projection_failure_rolls_back_files_and_keeps_outbox(self):
        before = (self.root / "data/todo/todo.txt").read_text(encoding="utf-8")
        self.database.apply_plan({
            "transactions": [], "journal": [],
            "todos": [{"created_date": "2026-08-02", "text": "失败测试", "tags": []}],
            "calendar": [], "clarifications": [],
        }, "2026-08-02")

        def fail():
            raise RuntimeError("maintenance failed")

        with self.assertRaisesRegex(RuntimeError, "maintenance failed"):
            project_pending(self.database, self.root, prepare=fail)
        self.assertEqual((self.root / "data/todo/todo.txt").read_text(encoding="utf-8"), before)
        self.assertEqual(self.database.maintenance_status()["pending"], 1)
        self.assertIn("maintenance failed", self.database.maintenance_status()["lastError"])

    def test_category_add_delete_and_migrate_descendants(self):
        self.database.apply_plan({
            "transactions": [{"date": "2026-08-02", "summary": "电影", "amount": "30", "account": "expenses:娱乐:电影", "note": ""}],
            "journal": [], "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-02")
        project_pending(self.database, self.root)
        self.assertEqual(self.database.create_category("健康:医疗"), "健康:医疗")
        self.assertIn("健康", self.database.list_categories())
        self.assertIn("健康:医疗", self.database.list_categories())
        self.assertEqual(self.database.delete_category("娱乐", "休闲"), 1)
        self.assertEqual(self.database.list_transactions()[0]["account"], "expenses:休闲:电影")
        project_pending(self.database, self.root)
        accounts = [item["account"] for item in LedgerRepository(self.root).list()]
        self.assertIn("expenses:休闲:电影", accounts)
        self.assertNotIn("expenses:娱乐:电影", accounts)

    def test_category_delete_without_target_marks_transactions_unclassified(self):
        self.assertEqual(self.database.delete_category("饮食", ""), 1)
        self.assertEqual(self.database.list_transactions()[0]["account"], "expenses")

    def test_budget_setting_and_excluded_transaction_survive_projection(self):
        self.assertEqual(self.database.set_monthly_budget("4321.5"), 4321.5)
        self.assertEqual(self.database.get_monthly_budget(), 4321.5)
        self.database.apply_plan({
            "transactions": [{
                "date": "2026-08-03", "summary": "预算外车票", "amount": "248",
                "account": "expenses:交通:火车", "note": "", "budget_excluded": True,
            }],
            "journal": [], "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-03")
        project_pending(self.database, self.root)
        item = next(item for item in LedgerRepository(self.root).list() if item["summary"] == "预算外车票")
        self.assertTrue(item["budget_excluded"])
        self.assertIn("budget: excluded", (self.root / "data/journal/2026.journal").read_text(encoding="utf-8"))

    def test_organizer_applies_category_and_tags_atomically_and_projects(self):
        self.database.apply_plan({
            "transactions": [{"date": "2026-08-04", "summary": "未分类早餐", "amount": "18", "account": "expenses", "note": ""}],
            "journal": [{"date": "2026-08-04", "text": "一段待补标签的日记", "tags": []}],
            "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-04")
        transaction = next(item for item in self.database.list_transactions() if item["summary"] == "未分类早餐")
        diary = next(item for item in self.database.list_diary() if item["text"] == "一段待补标签的日记")

        changed = self.database.apply_organizer(
            [{"id": transaction["id"], "account": "expenses:饮食"}],
            [{"id": diary["id"], "tags": ["生活", "早餐"]}],
        )

        self.assertEqual(changed, {"transactions": 1, "diary": 1, "todos": 0})
        self.assertEqual(next(item for item in self.database.list_transactions() if item["id"] == transaction["id"])["account"], "expenses:饮食")
        self.assertEqual(next(item for item in self.database.list_diary() if item["id"] == diary["id"])["tags"], ["生活", "早餐"])
        self.assertEqual(self.database.maintenance_status()["pending"], 4)
        project_pending(self.database, self.root)
        self.assertEqual(self.database.maintenance_status()["pending"], 0)

    def test_organizer_can_review_existing_categories_and_todo_tags(self):
        self.database.apply_plan({
            "transactions": [{"date": "2026-08-05", "summary": "已分类消费", "amount": "18", "account": "expenses:饮食", "note": ""}],
            "journal": [{"date": "2026-08-05", "text": "已有标签的日记", "tags": ["旧标签"]}],
            "todos": [{"created_date": "2026-08-05", "text": "已有标签的待办", "tags": ["旧标签"]}],
            "calendar": [], "clarifications": [],
        }, "2026-08-05")
        transaction = next(item for item in self.database.list_transactions() if item["summary"] == "已分类消费")
        diary = next(item for item in self.database.list_diary() if item["text"] == "已有标签的日记")
        todo = next(item for item in self.database.list_todos(include_completed=True) if item["text"] == "已有标签的待办")

        changed = self.database.apply_organizer(
            [{"id": transaction["id"], "account": "expenses"}],
            [{"id": diary["id"], "tags": ["新标签"]}],
            [{"id": todo["id"], "tags": ["工作"]}],
            allow_existing=True,
        )

        self.assertEqual(changed, {"transactions": 1, "diary": 1, "todos": 1})
        self.assertEqual(next(item for item in self.database.list_transactions() if item["id"] == transaction["id"])["account"], "expenses")
        self.assertEqual(next(item for item in self.database.list_diary() if item["id"] == diary["id"])["tags"], ["新标签"])
        self.assertEqual(next(item for item in self.database.list_todos(include_completed=True) if item["id"] == todo["id"])["tags"], ["工作"])

        with self.assertRaises(NotFoundError):
            self.database.apply_organizer(
                [{"id": transaction["id"], "account": "expenses:饮食"}],
                [{"id": "missing-diary", "tags": ["不会写入"]}],
                allow_existing=True,
            )
        self.assertEqual(next(item for item in self.database.list_transactions() if item["id"] == transaction["id"])["account"], "expenses")

    def test_organizer_review_tracks_batches_retries_and_change_history(self):
        transaction = self.database.list_transactions()[0]
        diary = self.database.list_diary()[0]
        review_id = self.database.create_organizer_review("all", None, [
            {"transactions": [transaction], "diary": [], "todos": []},
            {"transactions": [], "diary": [diary], "todos": []},
        ])

        self.assertEqual(self.database.organizer_review(review_id)["progress"], 0)
        first = self.database.claim_organizer_batch(review_id)
        self.assertEqual(first["number"], 1)
        self.database.fail_organizer_batch(review_id, 1, "AI 暂时不可用")
        failed = self.database.organizer_review(review_id)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failedBatches"], 1)
        self.database.retry_organizer_review(review_id)
        retried = self.database.claim_organizer_batch(review_id)
        self.assertEqual(retried["number"], 1)
        self.assertEqual(self.database.organizer_review(review_id)["batches"][0]["attempts"], 2)
        self.database.complete_organizer_batch(review_id, 1, {"transactions": [{"id": transaction["id"], "account": "expenses:饮食"}], "diary": [], "todos": []})
        second = self.database.claim_organizer_batch(review_id)
        self.assertEqual(second["number"], 2)
        self.database.complete_organizer_batch(review_id, 2, {"transactions": [], "diary": [{"id": diary["id"], "tags": ["生活"]}], "todos": []})
        self.assertEqual(self.database.organizer_review(review_id)["status"], "completed")

        self.database.apply_organizer(
            [{"id": transaction["id"], "account": "expenses:饮食:早餐"}],
            [{"id": diary["id"], "tags": ["生活", "早餐"]}],
            review_id=review_id,
            allow_existing=True,
        )
        changes = self.database.list_organizer_changes()
        self.assertEqual(len(changes), 2)
        self.assertTrue(any(item["field"] == "分类" and item["after"] == "expenses:饮食:早餐" for item in changes))
        self.assertTrue(any(item["field"] == "标签" and item["after"] == ["生活", "早餐"] for item in changes))


if __name__ == "__main__":
    unittest.main()
