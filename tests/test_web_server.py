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
            patch.object(web_server, "create_portable_archive", return_value=fake_archive) as create_archive,
            patch.object(web_server, "upload_archive", side_effect=web_server.BackupError("网络不可用")),
            patch.object(web_server.CONFIG, "secrets", return_value={"ai": {"api_key": "test-secret"}}),
        ):
            with self.assertRaisesRegex(web_server.WebError, "网络不可用"):
                web_server.backup_now()
        secrets_text = create_archive.call_args.kwargs["secrets_text"]
        self.assertIn("test-secret", secrets_text)
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

    def test_inbox_parse_failure_is_kept_for_retry(self):
        item = {"id": "inbox-1", "text": "一段待整理的话", "status": "pending"}
        failed = {**item, "status": "failed", "error": "AI 暂时不可用"}
        self.database.claim_inbox_item.return_value = item
        self.database.fail_inbox_item.return_value = failed
        with patch.object(web_server, "parse_ai", side_effect=web_server.WebError("AI 暂时不可用")):
            result = web_server.process_inbox_item("inbox-1")
        self.database.claim_inbox_item.assert_called_once_with("inbox-1")
        self.database.fail_inbox_item.assert_called_once()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["item"]["id"], "inbox-1")

    def test_inbox_clear_plan_is_applied_and_marks_local_backup_pending(self):
        item = {"id": "inbox-2", "text": "午饭 20 元", "status": "pending"}
        self.database.create_inbox_item.return_value = item
        self.database.claim_inbox_item.return_value = {**item, "status": "processing"}
        self.database.get_inbox_item.return_value = {**item, "status": "succeeded", "plan": PLAN}
        self.database.apply_inbox_plan.return_value = (PLAN, [], "succeeded")
        with patch.object(web_server, "parse_ai", return_value={"plan": PLAN}):
            result = web_server.create_inbox({"text": item["text"]})
        self.assertEqual(result["status"], "succeeded")
        self.database.apply_inbox_plan.assert_called_once_with("inbox-2", PLAN)
        self.worker.notify.assert_called_once()
        self.database.mark_backup_pending.assert_called_once()

    def test_finance_settings_store_budget_in_database(self):
        self.database.set_monthly_budget.return_value = 5000.0
        self.database.get_monthly_budget.return_value = 5000.0
        with patch.object(web_server.CONFIG, "update", return_value={"ai": {}, "backup": {}}):
            result = web_server.update_settings({"finance": {"monthlyBudget": "5000"}})
        self.database.set_monthly_budget.assert_called_once_with("5000")
        self.database.mark_backup_pending.assert_called_once()
        self.assertEqual(result["finance"]["monthlyBudget"], 5000.0)

    def test_organizer_apply_only_allows_current_unorganized_ids(self):
        snapshot = {
            "transactions": [{"id": "transaction-1", "account": "expenses"}],
            "diary": [{"id": "diary-1", "tags": []}],
            "categories": ["饮食"],
            "knownTags": [],
        }
        self.database.apply_organizer.return_value = {"transactions": 1, "diary": 1}
        with patch.object(web_server, "organizer_snapshot", return_value=snapshot):
            result = web_server.apply_organizer({
                "transactions": [{"id": "transaction-1", "account": "expenses:饮食"}],
                "diary": [{"id": "diary-1", "tags": ["生活"]}],
            })
        self.database.apply_organizer.assert_called_once()
        self.worker.notify.assert_called_once()
        self.assertEqual(result["transactions"], 1)
        self.assertEqual(result["diary"], 1)

    def test_organizer_suggestion_is_preview_only(self):
        snapshot = {
            "transactions": [{"id": "transaction-1", "account": "expenses", "summary": "午饭"}],
            "diary": [],
            "todos": [{"id": "todo-1", "text": "整理发票", "tags": []}],
            "categories": ["饮食"],
            "knownTags": [],
        }
        with (
            patch.object(web_server, "organizer_snapshot", return_value=snapshot),
            patch.object(web_server, "suggest_organizer_with_ai", return_value={"transactions": [{"id": "transaction-1", "account": "expenses:饮食"}], "diary": [], "todos": [{"id": "todo-1", "tags": ["工作"]}]}),
            patch.object(web_server.CONFIG, "ai_credentials", return_value={"enabled": True}),
        ):
            result = web_server.suggest_organizer({"transactionIds": ["transaction-1"], "diaryIds": [], "todoIds": ["todo-1"]})
        self.assertEqual(result["transactions"][0]["account"], "expenses:饮食")
        self.assertEqual(result["todos"][0]["tags"], ["工作"])
        self.database.apply_organizer.assert_not_called()

    def test_organizer_review_applies_existing_values_and_todo_tags(self):
        snapshot = {
            "scope": "all",
            "transactions": [{"id": "transaction-1", "account": "expenses:饮食"}],
            "diary": [{"id": "diary-1", "tags": ["旧标签"]}],
            "todos": [{"id": "todo-1", "tags": ["旧标签"]}],
            "categories": ["饮食"],
            "knownTags": ["旧标签"],
        }
        self.database.apply_organizer.return_value = {"transactions": 1, "diary": 1, "todos": 1}
        with patch.object(web_server, "organizer_snapshot", return_value=snapshot):
            result = web_server.apply_organizer({
                "scope": "all",
                "transactions": [{"id": "transaction-1", "account": "expenses"}],
                "diary": [{"id": "diary-1", "tags": []}],
                "todos": [{"id": "todo-1", "tags": ["工作"]}],
            })
        self.database.apply_organizer.assert_called_once_with(
            [{"id": "transaction-1", "account": "expenses"}],
            [{"id": "diary-1", "tags": []}],
            [{"id": "todo-1", "tags": ["工作"]}],
            allow_existing=True,
        )
        self.assertEqual(result["todos"], 1)

    def test_organizer_snapshot_filters_month_and_keeps_all_todos(self):
        self.database.list_transactions.return_value = [
            {"id": "transaction-aug", "date": "2026-08-31", "account": "expenses"},
            {"id": "transaction-sep", "date": "2026-09-01", "account": "expenses:饮食"},
        ]
        self.database.list_diary.return_value = [
            {"id": "diary-aug", "date": "2026-08-31", "tags": []},
            {"id": "diary-sep", "date": "2026-09-01", "tags": ["生活"]},
        ]
        self.database.list_todos.return_value = [
            {"id": "todo-aug", "date": "2026-08-31", "tags": [], "completed": True},
            {"id": "todo-sep", "date": "2026-09-01", "tags": ["工作"], "completed": False},
        ]
        self.database.list_categories.return_value = ["饮食"]

        snapshot = web_server.organizer_snapshot("month", "2026-08")

        self.assertEqual([item["id"] for item in snapshot["transactions"]], ["transaction-aug"])
        self.assertEqual([item["id"] for item in snapshot["diary"]], ["diary-aug"])
        self.assertEqual([item["id"] for item in snapshot["todos"]], ["todo-aug"])
        self.assertEqual(snapshot["knownTags"], ["工作", "生活"])

    def test_organizer_review_starts_as_persisted_batches(self):
        snapshot = {
            "transactions": [{"id": "transaction-1", "date": "2026-08-31", "account": "expenses"}],
            "diary": [], "todos": [], "categories": ["饮食"], "knownTags": [],
        }
        self.database.create_organizer_review.return_value = "review-1"
        self.database.organizer_review.return_value = {"id": "review-1", "status": "pending", "totalBatches": 1}
        with patch.object(web_server, "organizer_snapshot", return_value=snapshot):
            result = web_server.start_organizer_review({"transactionIds": ["transaction-1"]})
        self.assertEqual(result["id"], "review-1")
        batches = self.database.create_organizer_review.call_args.args[2]
        self.assertEqual(batches[0]["transactions"][0]["id"], "transaction-1")

    def test_organizer_review_failure_is_persisted_for_retry(self):
        batch = {"number": 1, "records": {"transactions": [{"id": "transaction-1", "account": "expenses"}], "diary": [], "todos": []}}
        self.database.claim_organizer_batch.return_value = batch
        self.database.organizer_review.side_effect = [
            {"id": "review-1", "scope": "unorganized", "month": None, "status": "running"},
            {"id": "review-1", "status": "failed", "failedBatches": 1, "lastError": "AI 暂时不可用"},
        ]
        with (
            patch.object(web_server, "organizer_snapshot", return_value={"categories": ["饮食"], "knownTags": []}),
            patch.object(web_server, "suggest_organizer_with_ai", side_effect=web_server.WebError("AI 暂时不可用")),
        ):
            result = web_server.process_organizer_review("review-1")
        self.database.fail_organizer_batch.assert_called_once_with("review-1", 1, "AI 暂时不可用")
        self.assertEqual(result["status"], "failed")

    def test_bulk_edit_updates_all_supported_record_types_in_one_database_call(self):
        self.database.apply_organizer.return_value = {"transactions": 1, "diary": 1, "todos": 1}

        result = web_server.bulk_edit({
            "transactions": [{"id": "transaction-1", "account": "expenses:饮食"}],
            "diary": [{"id": "diary-1", "tags": ["生活", "旅行"]}],
            "todos": [{"id": "todo-1", "tags": ["工作"]}],
        })

        self.database.apply_organizer.assert_called_once_with(
            [{"id": "transaction-1", "account": "expenses:饮食"}],
            [{"id": "diary-1", "tags": ["生活", "旅行"]}],
            [{"id": "todo-1", "tags": ["工作"]}],
            allow_existing=True,
        )
        self.worker.notify.assert_called_once()
        self.database.mark_backup_pending.assert_called_once()
        self.assertEqual(result["message"], "已批量修改 1 笔账目、1 篇日记和 1 项待办")

    def test_bulk_edit_rejects_empty_selection(self):
        with self.assertRaisesRegex(web_server.WebError, "请先选择需要编辑的记录"):
            web_server.bulk_edit({"transactions": [], "diary": [], "todos": []})

        self.database.apply_organizer.assert_not_called()
        self.worker.notify.assert_not_called()

    def test_bulk_edit_rejects_invalid_record_identifier(self):
        with self.assertRaisesRegex(web_server.WebError, "批量编辑的账目编号无效"):
            web_server.bulk_edit({
                "transactions": [{"id": "transaction/invalid", "account": "expenses"}],
                "diary": [],
                "todos": [],
            })

        self.database.apply_organizer.assert_not_called()

    def test_bulk_edit_failure_does_not_schedule_projection(self):
        self.database.apply_organizer.side_effect = web_server.DailyLogError("记录不存在")

        with self.assertRaisesRegex(web_server.DailyLogError, "记录不存在"):
            web_server.bulk_edit({
                "transactions": [{"id": "transaction-1", "account": "expenses"}],
                "diary": [],
                "todos": [],
            })

        self.worker.notify.assert_not_called()
        self.database.mark_backup_pending.assert_not_called()


if __name__ == "__main__":
    unittest.main()
