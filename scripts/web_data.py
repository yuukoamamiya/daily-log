#!/usr/bin/env python3
"""Build the read model used by the local Daily Log web application."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from daily_log.database import DailyLogDatabase


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(f"{month}-01")
    except ValueError:
        today = date.today()
        start = today.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def build_dashboard(
    month: str | None = None,
    database: DailyLogDatabase | None = None,
    extra_events: list[dict] | None = None,
) -> dict:
    if database is None:
        database = DailyLogDatabase()
    today = date.today()
    month = month or today.strftime("%Y-%m")
    month_start, month_end = _month_bounds(month)
    transactions = database.list_transactions()
    declared_categories = database.list_categories()
    diary = database.list_diary()
    all_todos = database.list_todos(include_completed=True)
    todos = [item for item in all_todos if not item["completed"]]
    completed_todos = [item for item in all_todos if item["completed"]]
    events = database.list_events()
    if extra_events:
        events = sorted([*events, *extra_events], key=lambda item: (item["date"], item.get("start", ""), item["title"]))
    budget = database.get_monthly_budget()

    month_transactions = [item for item in transactions if month_start.isoformat() <= item["date"] < month_end.isoformat()]
    month_spend = sum(Decimal(str(item["amount"])) for item in month_transactions)
    budget_spend = sum(
        Decimal(str(item["amount"])) for item in month_transactions if not item.get("budget_excluded", False)
    )
    week_start = today - timedelta(days=6)
    week_spend = sum(
        Decimal(str(item["amount"]))
        for item in transactions
        if week_start.isoformat() <= item["date"] <= today.isoformat()
    )

    categories: dict[str, Decimal] = defaultdict(Decimal)
    category_details: dict[str, Decimal] = defaultdict(Decimal)
    category_tree: dict[str, set[str]] = defaultdict(set)
    for category in declared_categories:
        parent, *children = category.split(":")
        if children:
            category_tree[parent].add(":".join(children))
        else:
            category_tree[parent]
    daily: dict[str, Decimal] = defaultdict(Decimal)
    for item in month_transactions:
        # Bare ``expenses`` is the aggregate/unclassified parent account,
        # not a real category.  It still belongs in totals and daily spending,
        # but must not appear in the category breakdown.
        if item["account"] != "expenses":
            category = item["category"]
            parent, *children = category.split(":")
            categories[parent] += Decimal(str(item["amount"]))
            category_details[category] += Decimal(str(item["amount"]))
            if children:
                category_tree[parent].add(":".join(children))
            else:
                category_tree[parent]
        daily[item["date"]] += Decimal(str(item["amount"]))

    diary_days = len({item["date"] for item in diary if month_start.isoformat() <= item["date"] < month_end.isoformat()})
    category_rows = [
        {"name": name, "amount": float(amount)}
        for name, amount in sorted(categories.items(), key=lambda item: item[1], reverse=True)
    ]
    category_detail_rows = [
        {"name": name, "amount": float(amount), "label": name.replace(":", " · ")}
        for name, amount in sorted(category_details.items(), key=lambda item: item[1], reverse=True)
    ]
    daily_rows = [
        {"date": key, "amount": float(value)}
        for key, value in sorted(daily.items())
    ]

    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "month": month_start.strftime("%Y-%m"),
        "summary": {
            "monthSpend": float(month_spend),
            "budgetSpend": float(budget_spend),
            "weekSpend": float(week_spend),
            "budget": budget,
            "budgetPercent": round(float(budget_spend) / budget * 100, 1) if budget else 0,
            "todoCount": len(todos),
            "diaryDays": diary_days,
        },
        "categories": category_rows,
        "categoryDetails": category_detail_rows,
        "categoryTree": [
            {"name": name, "children": sorted(children)}
            for name, children in sorted(category_tree.items())
        ],
        "dailySpending": daily_rows,
        "transactions": transactions,
        "diary": diary,
        "todos": todos,
        "completedTodos": completed_todos,
        "events": events,
    }


if __name__ == "__main__":
    import json
    import sys

    selected_month = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(build_dashboard(selected_month), ensure_ascii=False, indent=2))
