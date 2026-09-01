import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import web_server  # noqa: E402


PLAN = {
    "journal": [],
    "transactions": [{
        "date": "2026-08-29", "summary": "午饭", "note": "", "amount": "20.00", "account": "expenses:饮食",
    }],
    "todos": [], "calendar": [], "clarifications": [],
}


class WebServerLocalFirstTest(unittest.TestCase):
    def setUp(self):
        web_server.LAST_BACKUP_ERROR = None
        web_server.BACKUP_PENDING = False
        self.database = Mock()
        self.database.apply_plan.return_value = (PLAN, [])
        self.database.backup_state.return_value = {"pending": True, "lastBackupAt": None, "lastBackupTarget": None}
        self.worker = Mock()
        self.worker.status.return_value = {"pending": 1, "busy": False, "lastError": None}
        self.database_patch = patch.object(web_server, "database", return_value=self.database)
        self.worker_patch = patch.object(web_server, "worker", return_value=self.worker)
        self.database_patch.start()
        self.worker_patch.start()

    def tearDown(self):
        self.database_patch.stop()
        self.worker_patch.stop()

    def test_apply_plan_returns_before_projection_or_git(self):
        with (
            patch.object(web_server, "normalize_plan", return_value=PLAN),
        ):
            result = web_server.apply_plan(PLAN)

        self.assertEqual(result["message"], "已保存到本地，正在后台整理")
        self.database.apply_plan.assert_called_once()
        self.worker.notify.assert_called_once()
        self.assertEqual(result["summary"], "账目 1 条")

    def test_manual_entries_allow_unclassified_expense_and_tagless_diary(self):
        raw = {
            "journal": [{"date": "2026-08-31", "text": "没有标签的日记", "tags": []}],
            "transactions": [{
                "date": "2026-08-31", "summary": "未分类消费", "note": "", "amount": "8", "account": "",
            }],
            "todos": [], "calendar": [], "clarifications": [],
        }
        normalized = web_server.normalize_plan(raw, "2026-08-31")
        self.database.apply_plan.return_value = (normalized, [])

        result = web_server.apply_plan(raw)
        applied = self.database.apply_plan.call_args.args[0]
        self.assertEqual(applied["transactions"][0]["account"], "expenses")
        self.assertEqual(applied["journal"][0]["tags"], [])
        self.assertEqual(result["summary"], "账目 1 条，日记 1 条")

        diary = {"date": "2026-08-31", "text": "修改后仍不打标签", "tags": []}
        web_server.update_item("diary", "diary-untagged", diary)
        self.database.update.assert_called_once_with("diary", "diary-untagged", diary)

    def test_backup_flushes_projection_first_and_exposes_failure(self):
        fake_archive = ROOT / "fake-backup.zip"
        with (
            patch.object(web_server, "create_portable_archive", return_value=fake_archive),
            patch.object(web_server, "upload_archive", side_effect=web_server.BackupError("网络不可用")),
        ):
            with self.assertRaisesRegex(web_server.WebError, "网络不可用"):
                web_server.backup_now()
        self.worker.flush.assert_called_once()
        status = web_server.get_backup_status()
        self.assertEqual(status["lastError"], "网络不可用")

    def test_update_item_uses_database_and_schedules_projection(self):
        item = {"created_date": "2026-08-29", "text": "修改待办", "tags": ["测试"]}
        result = web_server.update_item("todo", "todo-123", item)
        self.database.update.assert_called_once_with("todo", "todo-123", item)
        self.worker.notify.assert_called_once()
        self.assertEqual(result["maintenance"]["pending"], 1)

    def test_delete_complete_and_restore_use_stable_identifier(self):
        web_server.delete_item("diary", "diary-123")
        web_server.change_todo("todo-456", "complete")
        web_server.change_todo("todo-456", "restore")
        self.database.delete.assert_called_once_with("diary", "diary-123")
        self.database.complete_todo.assert_called_once_with("todo-456")
        self.database.restore_todo.assert_called_once_with("todo-456")

    def test_category_create_and_delete_are_local_first(self):
        self.database.create_category.return_value = "健康"
        self.database.delete_category.return_value = 3
        created = web_server.create_category("健康")
        result = web_server.delete_category("娱乐", "休闲")
        self.database.create_category.assert_called_once_with("健康")
        self.database.delete_category.assert_called_once_with("娱乐", "休闲")
        self.worker.notify.assert_called_once()
        self.assertEqual(created["category"], "健康")
        self.assertEqual(result["count"], 3)

    def test_ai_record_parses_and_writes_without_preview_step(self):
        with (
            patch.object(web_server, "parse_ai", return_value={"plan": PLAN}) as parse_ai,
            patch.object(web_server, "apply_plan", return_value={"message": "saved", "summary": "一笔账"}) as apply_plan,
        ):
            result = web_server.record_with_ai("午饭20")
        parse_ai.assert_called_once_with("午饭20")
        apply_plan.assert_called_once_with(PLAN)
        self.assertEqual(result["message"], "AI 已整理并写入本地数据库")

    def test_finance_settings_store_budget_in_database(self):
        self.database.set_monthly_budget.return_value = 5000.0
        self.database.get_monthly_budget.return_value = 5000.0
        with patch.object(web_server.CONFIG, "update", return_value={"ai": {}, "backup": {}}):
            result = web_server.update_settings({"finance": {"monthlyBudget": "5000"}})
        self.database.set_monthly_budget.assert_called_once_with("5000")
        self.database.mark_backup_pending.assert_called_once()
        self.assertEqual(result["finance"]["monthlyBudget"], 5000.0)


if __name__ == "__main__":
    unittest.main()
