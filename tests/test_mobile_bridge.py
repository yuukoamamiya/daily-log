import tempfile
import threading
import time
import unittest
from pathlib import Path

from daily_log.database import DailyLogDatabase
from daily_log.mobile_bridge import (
    MobileBridgeService,
    MobileBridgeWorker,
    MockMobileBridgeProvider,
    build_dashboard_snapshot,
    dashboard_snapshot_json,
)


class MobileBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = DailyLogDatabase(Path(self.temp.name) / "daily-log.db")
        self.database.initialize_empty()

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_is_complete_and_json_is_deterministic(self):
        self.database.apply_plan({
            "transactions": [{"date": "2026-08-20", "summary": "午饭", "amount": "25", "account": "expenses:饮食"}],
            "journal": [{"date": "2026-08-20", "time": "12:00", "text": "今天阳光很好", "tags": []}],
            "todos": [{"created_date": "2026-08-20", "text": "散步", "tags": []}],
            "calendar": [{"date": "2026-08-21", "title": "看电影", "all_day": True}],
            "clarifications": [],
        }, "2026-08-20")
        snapshot = build_dashboard_snapshot(self.database, "2026-08")
        self.assertEqual(snapshot["snapshotType"], "daily-log-dashboard")
        self.assertEqual(snapshot["schemaVersion"], 1)
        for key in ("transactions", "diary", "todos", "completedTodos", "events"):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["diary"][0]["text"], "今天阳光很好")
        self.assertEqual(dashboard_snapshot_json(snapshot), dashboard_snapshot_json(dict(reversed(snapshot.items()))))

    def test_pull_is_idempotent_and_processes_only_new_items(self):
        provider = MockMobileBridgeProvider([
            {"id": "phone-1", "text": "手机输入一"},
            {"id": "phone-2", "text": "手机输入二"},
        ])
        service = MobileBridgeService(self.database, provider)
        processed = []

        def process(identifier):
            processed.append(identifier)
            self.database.claim_inbox_item(identifier)
            self.database.apply_inbox_plan(identifier, {
                "transactions": [],
                "journal": [{"date": "2026-08-20", "time": "09:00", "text": "已处理", "tags": []}],
                "todos": [], "calendar": [], "clarifications": [],
            }, "2026-08-20")

        first = service.pull_inbox(process_inbox=process)
        second = service.pull_inbox(process_inbox=process)

        self.assertEqual(first["imported"], 2)
        self.assertEqual(first["processed"], 2)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["duplicates"], 2)
        self.assertEqual(len(processed), 2)
        self.assertEqual(len(self.database.list_inbox_items()), 2)

    def test_pull_retries_a_local_failed_item(self):
        provider = MockMobileBridgeProvider([{"id": "phone-1", "text": "需要重试"}])
        service = MobileBridgeService(self.database, provider)
        service.pull_inbox()
        local = self.database.get_inbox_item_by_source("mock", "phone-1")
        self.database.claim_inbox_item(local["id"])
        self.database.fail_inbox_item(local["id"], "AI 暂时不可用")
        retried = []

        result = service.pull_inbox(process_inbox=lambda identifier: retried.append(identifier))

        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(retried, [local["id"]])

    def test_publish_and_pull_fail_independently(self):
        provider = MockMobileBridgeProvider(
            [{"id": "phone-1", "text": "留在手机上的内容"}],
            read_failures=1,
            publish_failures=1,
        )
        service = MobileBridgeService(self.database, provider)

        first = service.sync_once()
        self.assertFalse(first["ok"])
        self.assertFalse(first["published"]["ok"])
        self.assertFalse(first["pulled"]["ok"])
        self.assertEqual(self.database.list_inbox_items(), [])

        second = service.sync_once()
        self.assertTrue(second["ok"])
        self.assertEqual(second["pulled"]["imported"], 1)
        self.assertEqual(len(provider.published_dashboards), 1)

    def test_worker_retries_a_failed_bridge_in_background(self):
        provider = MockMobileBridgeProvider(publish_failures=1)
        service = MobileBridgeService(self.database, provider)
        published = threading.Event()
        worker = MobileBridgeWorker(service, debounce_seconds=0, retry_seconds=0.02)
        worker.start()
        try:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if provider.published_dashboards:
                    published.set()
                    break
                time.sleep(0.01)
            self.assertTrue(published.is_set())
            self.assertIsNone(worker.status()["lastError"])
        finally:
            worker.stop()


if __name__ == "__main__":
    unittest.main()
