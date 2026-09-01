import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from web_data import build_dashboard  # noqa: E402
from daily_log.database import DailyLogDatabase  # noqa: E402


class WebDataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = DailyLogDatabase(self.root / "daily-log.db")
        self.database.initialize_empty()

    def tearDown(self):
        self.temp.cleanup()

    def test_dashboard_aggregates_selected_month(self):
        self.database.set_monthly_budget("100")
        self.database.apply_plan({
            "transactions": [
                {"date": "2026-08-20", "summary": "午饭", "amount": "25.50", "account": "expenses:饮食"},
                {"date": "2026-09-01", "summary": "晚饭", "amount": "30.00", "account": "expenses:饮食"},
            ],
            "journal": [], "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-20")
        dashboard = build_dashboard("2026-08", self.database)
        self.assertEqual(dashboard["summary"]["monthSpend"], 25.5)
        self.assertEqual(dashboard["summary"]["budgetPercent"], 25.5)

    def test_dashboard_excludes_parent_expenses_from_categories(self):
        self.database.apply_plan({
            "transactions": [
                {"date": "2026-08-20", "summary": "期间汇总", "amount": "100.00", "account": "expenses"},
                {"date": "2026-08-21", "summary": "午饭", "amount": "25.00", "account": "expenses:饮食"},
            ],
            "journal": [], "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-20")
        dashboard = build_dashboard("2026-08", self.database)
        self.assertEqual(dashboard["summary"]["monthSpend"], 125.0)
        self.assertEqual(dashboard["categories"], [{"name": "饮食", "amount": 25.0}])

    def test_database_budget_excludes_only_marked_spending_from_progress(self):
        self.database.set_monthly_budget("100")
        self.database.apply_plan({
            "transactions": [
                {"date": "2026-08-20", "summary": "午饭", "amount": "25", "account": "expenses:饮食"},
                {"date": "2026-08-20", "summary": "车票", "amount": "40", "account": "expenses:交通", "budget_excluded": True},
            ],
            "journal": [], "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-20")
        dashboard = build_dashboard("2026-08", self.database)
        self.assertEqual(dashboard["summary"]["monthSpend"], 65.0)
        self.assertEqual(dashboard["summary"]["budgetSpend"], 25.0)
        self.assertEqual(dashboard["summary"]["budgetPercent"], 25.0)


if __name__ == "__main__":
    unittest.main()
