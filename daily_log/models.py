"""Validation and normalization for all daily-log write operations."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .errors import ValidationError


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
ACCOUNT_RE = re.compile(r"^expenses(?::[^:\s()]+)*$")


def clean_line(value: object, limit: int = 2_000) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text.replace(";", ",")[:limit]


def clean_journal(value: object, limit: int = 8_000) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:limit]


def normalize_tags(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("tags 必须是数组")
    result: list[str] = []
    for item in value:
        tag = clean_line(item, 40).lstrip("@#")
        if tag and " " not in tag and tag not in result:
            result.append(tag)
    return result


def validate_date(value: object, field: str) -> str:
    text = clean_line(value, 10)
    if not DATE_RE.fullmatch(text):
        raise ValidationError(f"{field} 日期格式无效")
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise ValidationError(f"{field} 日期无效") from error
    return text


def validate_optional_date(value: object, field: str) -> str | None:
    if value in (None, ""):
        return None
    return validate_date(value, field)


def validate_time(value: object, field: str, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise ValidationError(f"{field} 时间不能为空")
        return None
    text = clean_line(value, 5)
    if not TIME_RE.fullmatch(text):
        raise ValidationError(f"{field} 时间格式无效")
    hour, minute = (int(part) for part in text.split(":"))
    if hour > 23 or minute > 59:
        raise ValidationError(f"{field} 时间无效")
    return text


def _items(raw: dict, name: str) -> list:
    value = raw.get(name, [])
    if not isinstance(value, list):
        raise ValidationError(f"{name} 必须是数组")
    return value


def _check_time_order(start_time: str | None, end_time: str | None, title: str) -> None:
    if start_time and end_time and end_time <= start_time:
        raise ValidationError(f"{title} 结束时间必须晚于开始时间")


def normalize_plan(raw: object, today: str) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("输入不是 JSON 对象")
    clarifications = raw.get("clarifications", [])
    if not isinstance(clarifications, list):
        raise ValidationError("clarifications 必须是数组")
    plan = {
        "journal": [],
        "transactions": [],
        "todos": [],
        "calendar": [],
        "clarifications": [clean_line(item, 500) for item in clarifications if clean_line(item)],
    }

    for item in _items(raw, "journal"):
        if not isinstance(item, dict):
            raise ValidationError("journal 项必须是对象")
        text = clean_journal(item.get("text"))
        if not text:
            raise ValidationError("日记内容不能为空")
        plan["journal"].append({
            "date": validate_date(item.get("date", today), "日记"),
            "text": text,
            "tags": normalize_tags(item.get("tags")),
        })

    for item in _items(raw, "transactions"):
        if not isinstance(item, dict):
            raise ValidationError("transactions 项必须是对象")
        try:
            amount = Decimal(str(item.get("amount", "")))
        except (InvalidOperation, ValueError) as error:
            raise ValidationError("金额格式无效") from error
        if not amount.is_finite() or amount <= 0:
            raise ValidationError("金额必须是正数")
        account = clean_line(item.get("account") or "expenses", 200)
        if account.startswith("(") and account.endswith(")"):
            account = account[1:-1]
        if not ACCOUNT_RE.fullmatch(account):
            account = "expenses"
        summary = clean_line(item.get("summary"), 120)
        if not summary:
            raise ValidationError("账目摘要不能为空")
        plan["transactions"].append({
            "date": validate_date(item.get("date", today), "账目"),
            "summary": summary,
            "note": clean_line(item.get("note"), 500),
            "amount": f"{amount:.2f}",
            "account": account,
            "budget_excluded": bool(item.get("budget_excluded", False)),
        })

    for item in _items(raw, "todos"):
        if not isinstance(item, dict):
            raise ValidationError("todos 项必须是对象")
        action = clean_line(item.get("action"), 20).lower()
        text = clean_line(item.get("text"))
        if not text:
            raise ValidationError("待办内容不能为空")
        normalized = {
            "text": text,
            "created_date": validate_date(item.get("created_date") or today, "待办"),
            "due_date": validate_optional_date(item.get("due_date") or item.get("reminder_date"), "待办截止"),
            "tags": normalize_tags(item.get("tags")),
        }
        if action in {"done", "delete", "restore"}:
            normalized["action"] = action
        plan["todos"].append(normalized)

    for item in _items(raw, "calendar"):
        if not isinstance(item, dict):
            raise ValidationError("calendar 项必须是对象")
        title = clean_line(item.get("title"))
        if not title:
            raise ValidationError("日程标题不能为空")
        action = clean_line(item.get("action"), 20).lower()
        normalized = {"title": title}
        if action == "move":
            normalized["action"] = action
            normalized["old_date"] = validate_date(item.get("old_date"), "日程原日期")
        elif action == "delete":
            normalized["action"] = action
        normalized["date"] = validate_date(item.get("date"), "日程")
        if action != "delete":
            start_time = validate_time(item.get("start_time"), "日程开始")
            end_time = validate_time(item.get("end_time"), "日程结束")
            _check_time_order(start_time, end_time, title)
            normalized.update({
                "start_time": start_time,
                "end_time": end_time,
                "location": clean_line(item.get("location"), 200),
                "description": clean_line(item.get("description"), 1_000),
            })
        plan["calendar"].append(normalized)
    return plan


def normalize_item(kind: str, raw: object, today: str) -> dict:
    mapping = {"transaction": "transactions", "diary": "journal", "todo": "todos", "event": "calendar"}
    if kind not in mapping:
        raise ValidationError("未知的数据类型")
    plan = normalize_plan({mapping[kind]: [raw]}, today)
    return plan[mapping[kind]][0]
