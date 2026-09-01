"""Portable exports for other applications and Daily Log backups."""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from .database import DailyLogDatabase, default_state_dir
from .errors import ValidationError


def _safe_line(value: object) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _write_todos(database: DailyLogDatabase, root: Path) -> None:
    active = []
    done = []
    for item in database.list_todos(include_completed=True):
        text = f"{item['date']} {_safe_line(item['text'])}"
        if item["tags"]:
            text += " " + " ".join(f"@{_safe_line(tag)}" for tag in item["tags"])
        if item.get("dueDate"):
            text += f" due:{item['dueDate']}"
        if item["completed"]:
            completed = item["completedDate"] or item["date"]
            done.append(f"x {completed} {text}")
        else:
            active.append(text)
    todo_root = root / "todo"
    todo_root.mkdir(parents=True, exist_ok=True)
    (todo_root / "todo.txt").write_text("\n".join(active) + ("\n" if active else ""), encoding="utf-8")
    (todo_root / "done.txt").write_text("\n".join(done) + ("\n" if done else ""), encoding="utf-8")


def _write_diary(database: DailyLogDatabase, root: Path) -> None:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for item in database.list_diary():
        by_date[item["date"]].append(item)
    diary_root = root / "diary"
    diary_root.mkdir(parents=True, exist_ok=True)
    for entry_date, items in sorted(by_date.items()):
        lines = [f"# {entry_date}", ""]
        for item in reversed(items):
            if item["tags"]:
                lines.extend(["标签：" + " ".join(f"#{tag}" for tag in item["tags"]), ""])
            lines.extend([item["text"], ""])
        (diary_root / f"{entry_date}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_ledger_csv(database: DailyLogDatabase, root: Path) -> None:
    with (root / "ledger.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", "summary", "amount", "currency", "category", "budget_excluded", "note"])
        for item in reversed(database.list_transactions()):
            writer.writerow([
                item["date"], item["summary"], f"{item['amount']:.2f}", "CNY", item["category"],
                "true" if item.get("budget_excluded") else "false", item["note"],
            ])


def _write_calendar(database: DailyLogDatabase, root: Path) -> None:
    calendar = Calendar()
    calendar.add("prodid", "-//Daily Log//Portable Export//CN")
    calendar.add("version", "2.0")
    timezone = ZoneInfo("Asia/Shanghai")
    for item in database.list_events():
        event = Event()
        event.add("uid", item["uid"])
        event.add("summary", item["title"])
        if item["allDay"]:
            event.add("dtstart", date.fromisoformat(item["date"]))
        else:
            event.add("dtstart", datetime.fromisoformat(item["start"]).astimezone(timezone))
            if item["end"]:
                event.add("dtend", datetime.fromisoformat(item["end"]).astimezone(timezone))
        if item["location"]:
            event.add("location", item["location"])
        if item["description"]:
            event.add("description", item["description"])
        calendar.add_component(event)
    (root / "calendar.ics").write_bytes(calendar.to_ical())


def _write_org(database: DailyLogDatabase, root: Path) -> None:
    lines = ["#+TITLE: Daily Log", "#+LANGUAGE: zh-CN", ""]
    lines.append("* 待办")
    for item in database.list_todos(include_completed=True):
        state = "DONE" if item["completed"] else "TODO"
        tags = ":" + ":".join(item["tags"]) + ":" if item["tags"] else ""
        lines.append(f"** {state} {_safe_line(item['text'])} {tags}".rstrip())
        lines.append(f"   :PROPERTIES:\n   :CREATED: {item['date']}\n   :END:")
    lines.append("\n* 日记")
    for item in database.list_diary():
        tags = ":" + ":".join(item["tags"]) + ":" if item["tags"] else ""
        lines.extend([f"** {item['date']} {tags}".rstrip(), item["text"], ""])
    lines.append("* 账目")
    for item in database.list_transactions():
        lines.append(f"** {item['date']} {_safe_line(item['summary'])}")
        budget_label = " · 不计预算" if item.get("budget_excluded") else ""
        lines.append(f"   {item['amount']:.2f} CNY · {item['category']}{budget_label}" + (f" · {item['note']}" if item["note"] else ""))
    lines.append("\n* 日程")
    for item in database.list_events():
        timing = item["date"] if item["allDay"] else item["start"][:16].replace("T", " ")
        lines.extend([f"** {_safe_line(item['title'])}", f"   <{timing}>"])
    (root / "daily-log.org").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_categories(database: DailyLogDatabase, root: Path) -> None:
    categories = database.list_categories()
    (root / "categories.txt").write_text("\n".join(categories) + ("\n" if categories else ""), encoding="utf-8")


def _write_combined_todo(database: DailyLogDatabase, target: Path) -> None:
    lines = []
    for item in reversed(database.list_todos(include_completed=True)):
        text = f"{item['date']} {_safe_line(item['text'])}"
        if item["tags"]:
            text += " " + " ".join(f"@{_safe_line(tag)}" for tag in item["tags"])
        if item["completed"]:
            text = f"x {item['completedDate'] or item['date']} {text}"
        lines.append(text)
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_combined_diary(database: DailyLogDatabase, target: Path) -> None:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for item in database.list_diary():
        by_date[item["date"]].append(item)
    lines = ["# Daily Log 日记", ""]
    for entry_date, items in sorted(by_date.items(), reverse=True):
        lines.extend([f"## {entry_date}", ""])
        for item in reversed(items):
            if item["tags"]:
                lines.extend(["标签：" + " ".join(f"#{tag}" for tag in item["tags"]), ""])
            lines.extend([item["text"], ""])
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


EXPORT_FORMATS = {
    "expenses-csv": ("账目 CSV", "expenses.csv"),
    "diary-markdown": ("日记 Markdown", "diary.md"),
    "todo-txt": ("待办 todo.txt", "todo.txt"),
    "calendar-ics": ("日程 iCalendar", "calendar.ics"),
    "org": ("综合 Org Mode", "daily-log.org"),
}


def export_data_file(database: DailyLogDatabase, export_format: object, destination: Path | None = None) -> Path:
    """Export one interoperable format instead of an application backup bundle."""
    key = str(export_format or "").strip().lower()
    if key not in EXPORT_FORMATS:
        raise ValidationError("请选择支持的导出格式。")
    output_root = Path(destination or default_state_dir() / "exports")
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    filename = EXPORT_FORMATS[key][1]
    target = output_root / f"{Path(filename).stem}-{stamp}{Path(filename).suffix}"
    with tempfile.TemporaryDirectory() as directory:
        temporary_root = Path(directory)
        if key == "expenses-csv":
            _write_ledger_csv(database, temporary_root)
            source = temporary_root / "ledger.csv"
        elif key == "diary-markdown":
            source = temporary_root / "diary.md"
            _write_combined_diary(database, source)
        elif key == "todo-txt":
            source = temporary_root / "todo.txt"
            _write_combined_todo(database, source)
        elif key == "calendar-ics":
            _write_calendar(database, temporary_root)
            source = temporary_root / "calendar.ics"
        else:
            _write_org(database, temporary_root)
            source = temporary_root / "daily-log.org"
        shutil.copy2(source, target)
    return target


def create_portable_archive(
    database: DailyLogDatabase,
    *,
    destination: Path | None = None,
    include_database: bool = False,
    settings_text: str | None = None,
    include_portable: bool = True,
    portable_root: Path | None = None,
    secrets_blob: bytes | None = None,
    secrets_text: str | None = None,
) -> Path:
    output_root = Path(destination or default_state_dir() / "exports")
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    target = output_root / f"daily-log-{stamp}.zip"
    suffix = 1
    while target.exists():
        target = output_root / f"daily-log-{stamp}-{suffix}.zip"
        suffix += 1
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "daily-log"
        root.mkdir()
        if include_portable:
            _write_todos(database, root)
            _write_diary(database, root)
            _write_ledger_csv(database, root)
            _write_calendar(database, root)
            _write_org(database, root)
            _write_categories(database, root)
        if include_database and include_portable:
            database.snapshot_to(root / "daily-log.db")
        if settings_text and include_portable:
            (root / "config.portable.ini").write_text(settings_text, encoding="utf-8")
        if portable_root is not None and include_portable:
            source = Path(portable_root)
            if source.is_dir():
                shutil.copytree(source, root / "portable", ignore=shutil.ignore_patterns("*.tmp"))
        if secrets_blob:
            (root / "secrets.enc").write_bytes(secrets_blob)
        if secrets_text is not None:
            (root / "secrets.json").write_text(secrets_text, encoding="utf-8")
        manifest = {
            "format": 1,
            "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "includesData": bool(include_portable),
            "includesSecrets": bool(secrets_blob or secrets_text is not None),
        }
        (root / "backup-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
    return target
