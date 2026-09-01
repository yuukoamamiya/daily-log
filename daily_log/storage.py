"""Repositories for the client's portable text projections and legacy import."""
from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from .errors import ConflictError, NotFoundError
from .transaction import atomic_write


TRANSACTION_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.+?)(?:\s+;\s*(.*))?$")
TRANSACTION_POSTING = re.compile(r"^\s+\((expenses(?::[^)]+)?)\)\s+(-?[0-9]+(?:\.[0-9]+)?)\s*$")
DIARY_HEADER = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\s+([^]]+)]\s?(.*)$", re.MULTILINE)
TODO_ACTIVE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$")
TODO_DONE = re.compile(r"^x\s+(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(.+)$")
TAG = re.compile(r"(?<!\S)@([^\s@]+)")
DUE = re.compile(r"(?<!\S)due:(\d{4}-\d{2}-\d{2})(?!\S)")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _id(prefix: str, *parts: object, occurrence: int = 1) -> str:
    raw = "\x1f".join(str(part) for part in parts) + f"\x1f{occurrence}"
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _unique_ids(prefix: str, keys: list[tuple]) -> list[str]:
    counts: dict[tuple, int] = defaultdict(int)
    result = []
    for key in keys:
        counts[key] += 1
        result.append(_id(prefix, *key, occurrence=counts[key]))
    return result


def _write_text(path: Path, text: str) -> None:
    atomic_write(path, text.encode("utf-8"))


def _clean_diary_text(text: str) -> tuple[str, list[str]]:
    tags = list(dict.fromkeys(TAG.findall(text)))
    visible = TAG.sub("", text)
    visible = re.sub(r"[ \t]+\n", "\n", visible)
    visible = re.sub(r"[ \t]{2,}", " ", visible).strip()
    return visible, tags


class LedgerRepository:
    def __init__(self, root: Path):
        self.root = Path(root)

    def list(self) -> list[dict]:
        records: list[dict] = []
        keys: list[tuple] = []
        for path in sorted((self.root / "data/journal").glob("[0-9][0-9][0-9][0-9].journal")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines[:-1]):
                header = TRANSACTION_HEADER.match(line.strip())
                posting = TRANSACTION_POSTING.match(lines[index + 1])
                if not header or not posting:
                    continue
                raw_note = (header.group(3) or "").strip()
                budget_excluded = bool(re.search(r"(?:^|;\s*)budget:\s*excluded\s*$", raw_note))
                note = re.sub(r"(?:^|;\s*)budget:\s*excluded\s*$", "", raw_note).strip(" ;")
                key = (
                    path.name,
                    header.group(1),
                    header.group(2).strip(),
                    note,
                    posting.group(1),
                    f"{Decimal(posting.group(2)):.2f}",
                )
                keys.append(key)
                records.append({
                    "date": key[1], "summary": key[2], "note": key[3], "account": key[4],
                    "category": "未分类" if key[4] == "expenses" else key[4].removeprefix("expenses:"),
                    "amount": float(Decimal(key[5])), "_path": path, "_line": index,
                    "budget_excluded": budget_excluded,
                })
        for record, identifier in zip(records, _unique_ids("transaction", keys), strict=True):
            record["id"] = identifier
        return sorted(records, key=lambda item: (item["date"], item["id"]), reverse=True)

    def _existing_keys(self, path: Path) -> set[tuple[str, str, str, str]]:
        return {
            (item["date"], item["summary"], f"{item['amount']:.2f}", item["account"])
            for item in self.list() if item["_path"] == path
        }

    def add(self, item: dict, *, deduplicate: bool = True) -> None:
        year = item["date"][:4]
        directory = self.root / "data/journal"
        directory.mkdir(parents=True, exist_ok=True)
        year_path = directory / f"{year}.journal"
        if not year_path.exists():
            _write_text(year_path, f"; {year} 年流水账\n")
        key = (item["date"], item["summary"], item["amount"], item["account"])
        if deduplicate and key in self._existing_keys(year_path):
            return
        content = year_path.read_text(encoding="utf-8")
        if content and not content.endswith("\n"):
            content += "\n"
        if content and not content.endswith("\n\n"):
            content += "\n"
        comments = [item["note"]] if item.get("note") else []
        if item.get("budget_excluded"):
            comments.append("budget: excluded")
        note = f" ; {'; '.join(comments)}" if comments else ""
        content += f"{item['date']} {item['summary']}{note}\n    ({item['account']})    {item['amount']}\n"
        _write_text(year_path, content)
        ledger_path = directory / "ledger.journal"
        ledger = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else ""
        include = f"include {year}.journal"
        if include not in ledger.splitlines():
            if ledger and not ledger.endswith("\n"):
                ledger += "\n"
            ledger += include + "\n"
            _write_text(ledger_path, ledger)

    def _find(self, identifier: str) -> dict:
        for item in self.list():
            if item["id"] == identifier:
                return item
        raise NotFoundError("账目不存在或已经发生变化，请刷新后重试。")

    def delete(self, identifier: str) -> None:
        existing = self._find(identifier)
        path = existing["_path"]
        lines = path.read_text(encoding="utf-8").splitlines()
        del lines[existing["_line"]:existing["_line"] + 2]
        while len(lines) > 1 and lines[-1] == "" and lines[-2] == "":
            lines.pop()
        _write_text(path, "\n".join(lines).rstrip() + "\n")

    def update(self, identifier: str, item: dict) -> None:
        self._find(identifier)
        self.delete(identifier)
        self.add(item, deduplicate=False)


class DiaryRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "data/diary/journal.txt"

    def list(self) -> list[dict]:
        if not self.path.exists():
            return []
        content = self.path.read_text(encoding="utf-8")
        matches = list(DIARY_HEADER.finditer(content))
        records: list[dict] = []
        keys: list[tuple] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            continuation = content[match.end():end].strip("\n")
            raw_text = match.group(3) + (("\n" + continuation) if continuation else "")
            text, tags = _clean_diary_text(raw_text.strip())
            key = (match.group(1), match.group(2), text, "\x1e".join(tags))
            keys.append(key)
            records.append({
                "date": key[0], "time": key[1], "text": text, "tags": tags,
                "_start": match.start(), "_end": end,
            })
        for record, identifier in zip(records, _unique_ids("diary", keys), strict=True):
            record["id"] = identifier
        return sorted(records, key=lambda item: (item["date"], item["time"], item["id"]), reverse=True)

    @staticmethod
    def _render(item: dict, time_text: str = "09:00:00 AM") -> str:
        suffix = " ".join(f"@{tag}" for tag in item.get("tags", []))
        body = item["text"] + ((" " + suffix) if suffix else "")
        return f"[{item['date']} {time_text}] {body}\n\n"

    def add(self, item: dict) -> None:
        if any(entry["date"] == item["date"] and entry["text"] == item["text"] for entry in self.list()):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        if content and not content.endswith("\n\n"):
            content = content.rstrip() + "\n\n"
        _write_text(self.path, content + self._render(item))

    def _find(self, identifier: str) -> dict:
        for item in self.list():
            if item["id"] == identifier:
                return item
        raise NotFoundError("日记不存在或已经发生变化，请刷新后重试。")

    def delete(self, identifier: str) -> None:
        item = self._find(identifier)
        content = self.path.read_text(encoding="utf-8")
        updated = (content[:item["_start"]] + content[item["_end"]:]).lstrip("\n")
        _write_text(self.path, updated.rstrip() + ("\n" if updated.strip() else ""))

    def update(self, identifier: str, replacement: dict) -> None:
        item = self._find(identifier)
        content = self.path.read_text(encoding="utf-8")
        rendered = self._render(replacement, item["time"])
        updated = content[:item["_start"]] + rendered + content[item["_end"]:].lstrip("\n")
        _write_text(self.path, updated.rstrip() + "\n")


class TodoRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.todo_path = self.root / "data/todo/todo.txt"
        self.done_path = self.root / "data/todo/done.txt"

    @staticmethod
    def _visible(raw_text: str) -> tuple[str, list[str], str | None]:
        tags = list(dict.fromkeys(TAG.findall(raw_text)))
        due = DUE.search(raw_text)
        visible = DUE.sub("", TAG.sub("", raw_text))
        return re.sub(r"\s{2,}", " ", visible).strip(), tags, due.group(1) if due else None

    def list(self, include_completed: bool = False) -> list[dict]:
        records: list[dict] = []
        keys: list[tuple] = []
        if self.todo_path.exists():
            for line_number, raw in enumerate(self.todo_path.read_text(encoding="utf-8").splitlines()):
                match = TODO_ACTIVE.match(raw.strip())
                if not match:
                    continue
                text, tags, due_date = self._visible(match.group(2))
                key = ("active", match.group(1), match.group(2))
                keys.append(key)
                records.append({
                    "date": match.group(1), "dueDate": due_date, "text": text, "rawText": match.group(2), "tags": tags,
                    "completed": False, "completedDate": None, "_path": self.todo_path, "_line": line_number,
                })
        if include_completed and self.done_path.exists():
            for line_number, raw in enumerate(self.done_path.read_text(encoding="utf-8").splitlines()):
                match = TODO_DONE.match(raw.strip())
                if not match:
                    continue
                text, tags, due_date = self._visible(match.group(3))
                key = ("done", match.group(2), match.group(3), match.group(1))
                keys.append(key)
                records.append({
                    "date": match.group(2), "dueDate": due_date, "text": text, "rawText": match.group(3), "tags": tags,
                    "completed": True, "completedDate": match.group(1), "_path": self.done_path, "_line": line_number,
                })
        for record, identifier in zip(records, _unique_ids("todo", keys), strict=True):
            record["id"] = identifier
        return records

    def add(self, item: dict) -> None:
        suffix = " ".join(f"@{tag}" for tag in item.get("tags", []))
        due = f"due:{item['due_date']}" if item.get("due_date") else ""
        raw_text = f"{item['text']} {suffix} {due}".strip()
        if any(entry["date"] == item["created_date"] and entry["rawText"] == raw_text for entry in self.list()):
            return
        self.todo_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.todo_path.read_text(encoding="utf-8") if self.todo_path.exists() else ""
        if content and not content.endswith("\n"):
            content += "\n"
        _write_text(self.todo_path, content + f"{item['created_date']} {raw_text}\n")

    def _find(self, identifier: str, include_completed: bool = True) -> dict:
        for item in self.list(include_completed=include_completed):
            if item["id"] == identifier:
                return item
        raise NotFoundError("待办不存在或已经发生变化，请刷新后重试。")

    @staticmethod
    def _delete_line(path: Path, line_number: int) -> None:
        lines = path.read_text(encoding="utf-8").splitlines()
        del lines[line_number]
        _write_text(path, "\n".join(lines) + ("\n" if lines else ""))

    def delete(self, identifier: str) -> None:
        item = self._find(identifier)
        self._delete_line(item["_path"], item["_line"])

    def update(self, identifier: str, replacement: dict) -> None:
        item = self._find(identifier, include_completed=False)
        suffix = " ".join(f"@{tag}" for tag in replacement.get("tags", []))
        due = f"due:{replacement['due_date']}" if replacement.get("due_date") else ""
        raw_text = f"{replacement['text']} {suffix} {due}".strip()
        lines = self.todo_path.read_text(encoding="utf-8").splitlines()
        lines[item["_line"]] = f"{replacement['created_date']} {raw_text}"
        _write_text(self.todo_path, "\n".join(lines) + "\n")

    def complete(self, identifier: str, completed_date: str) -> None:
        item = self._find(identifier, include_completed=False)
        done = self.done_path.read_text(encoding="utf-8") if self.done_path.exists() else ""
        if done and not done.endswith("\n"):
            done += "\n"
        _write_text(self.done_path, done + f"x {completed_date} {item['date']} {item['rawText']}\n")
        self._delete_line(self.todo_path, item["_line"])

    def restore(self, identifier: str) -> None:
        item = self._find(identifier, include_completed=True)
        if not item["completed"]:
            raise ConflictError("这项待办尚未完成。")
        current = self.todo_path.read_text(encoding="utf-8") if self.todo_path.exists() else ""
        if current and not current.endswith("\n"):
            current += "\n"
        _write_text(self.todo_path, current + f"{item['date']} {item['rawText']}\n")
        self._delete_line(self.done_path, item["_line"])

    def find_legacy(self, created_date: str, text: str, include_completed: bool = False) -> dict | None:
        stripped = TAG.sub("", text).strip()
        for item in self.list(include_completed=include_completed):
            if item["date"] == created_date and (item["rawText"] == text or item["text"] == stripped):
                return item
        return None


class CalendarRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.directory = self.root / "data/calendar"

    def list(self) -> list[dict]:
        events: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for path in sorted(self.directory.glob("*.ics")):
            try:
                calendar = Calendar.from_ical(path.read_bytes())
            except Exception:
                continue
            for component in calendar.walk("VEVENT"):
                start_field = component.get("DTSTART")
                if start_field is None:
                    continue
                start = start_field.dt
                end_field = component.get("DTEND")
                end = end_field.dt if end_field is not None else None
                uid = str(component.get("UID", path.stem))
                start_iso = start.isoformat() if hasattr(start, "isoformat") else str(start)
                if (uid, start_iso) in seen:
                    continue
                seen.add((uid, start_iso))
                all_day = isinstance(start, date) and not isinstance(start, datetime)
                events.append({
                    "id": f"event-{uid}", "uid": uid, "title": str(component.get("SUMMARY", "未命名日程")),
                    "start": start_iso, "end": end.isoformat() if end is not None else "", "date": start_iso[:10],
                    "allDay": all_day, "location": str(component.get("LOCATION", "")),
                    "description": str(component.get("DESCRIPTION", "")), "_path": path,
                })
        return sorted(events, key=lambda item: (item["start"], item["title"]))

    def _find(self, identifier: str) -> tuple[Path, Calendar, Event]:
        uid = identifier.removeprefix("event-")
        for path in self.directory.glob("*.ics"):
            try:
                calendar = Calendar.from_ical(path.read_bytes())
            except Exception:
                continue
            for event in calendar.walk("VEVENT"):
                if str(event.get("UID", "")) == uid:
                    return path, calendar, event
        raise NotFoundError("日程不存在或已经发生变化，请刷新后重试。")

    @staticmethod
    def _apply_fields(event: Event, item: dict) -> None:
        start_time = item.get("start_time")
        year, month, day = (int(part) for part in item["date"].split("-"))
        if start_time:
            hour, minute = (int(part) for part in start_time.split(":"))
            start = datetime(year, month, day, hour, minute, tzinfo=LOCAL_TIMEZONE)
            end = None
            if item.get("end_time"):
                end_hour, end_minute = (int(part) for part in item["end_time"].split(":"))
                end = datetime(year, month, day, end_hour, end_minute, tzinfo=LOCAL_TIMEZONE)
        else:
            start = date(year, month, day)
            end = start + timedelta(days=1)
        for field in ("SUMMARY", "DTSTART", "DTEND", "LOCATION", "DESCRIPTION", "DTSTAMP"):
            event.pop(field, None)
        event.add("SUMMARY", item["title"])
        event.add("DTSTART", start)
        if end is not None:
            event.add("DTEND", end)
        if item.get("location"):
            event.add("LOCATION", item["location"])
        if item.get("description"):
            event.add("DESCRIPTION", item["description"])
        event.add("DTSTAMP", datetime.now(timezone.utc))

    def add(self, item: dict) -> None:
        if any(event["date"] == item["date"] and event["title"] == item["title"] for event in self.list()):
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex.upper()
        calendar = Calendar()
        calendar.add("VERSION", "2.0")
        calendar.add("PRODID", "-//daily-log//local core//EN")
        event = Event()
        event.add("UID", uid)
        self._apply_fields(event, item)
        calendar.add_component(event)
        atomic_write(self.directory / f"{uid}.ics", calendar.to_ical())

    def update(self, identifier: str, item: dict) -> None:
        path, calendar, event = self._find(identifier)
        self._apply_fields(event, item)
        atomic_write(path, calendar.to_ical())

    def delete(self, identifier: str) -> None:
        path, calendar, event = self._find(identifier)
        calendar.subcomponents.remove(event)
        if any(component.name == "VEVENT" for component in calendar.walk()):
            atomic_write(path, calendar.to_ical())
        else:
            path.unlink()

    def find_legacy(self, event_date: str, title: str) -> dict | None:
        return next((item for item in self.list() if item["date"] == event_date and item["title"] == title), None)
