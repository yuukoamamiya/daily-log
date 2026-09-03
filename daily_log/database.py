"""SQLite operational store for the local client.

SQLite is the only writable source used by the desktop/web client.  Standard
text files remain portable projections that are updated by the background
projector.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from .errors import ConflictError, NotFoundError, ValidationError
from .data_location import read_redirect, system_default_state_dir
from .models import ACCOUNT_RE, normalize_item, normalize_plan, normalize_tags
from .storage import CalendarRepository, DiaryRepository, LedgerRepository, TodoRepository


SCHEMA_VERSION = 8
DEFAULT_MONTHLY_BUDGET = Decimal("3000.00")


def default_state_dir() -> Path:
    override = os.environ.get("DAILY_LOG_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return read_redirect() or system_default_state_dir()


def default_database_path() -> Path:
    return default_state_dir() / "daily-log.db"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _tags(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


class DailyLogDatabase:
    """Small connection-per-operation SQLite store with a durable outbox."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or default_database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self.session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    entry_date TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    amount TEXT NOT NULL,
                    account TEXT NOT NULL,
                    budget_excluded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS diary_entries (
                    id TEXT PRIMARY KEY,
                    entry_date TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    text TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    created_date TEXT NOT NULL,
                    due_date TEXT,
                    text TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    completed INTEGER NOT NULL DEFAULT 0,
                    completed_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    uid TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    all_day INTEGER NOT NULL DEFAULT 1,
                    location TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projection_map (
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    PRIMARY KEY (entity_kind, entity_id)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_created ON outbox(id);
                CREATE INDEX IF NOT EXISTS idx_todos_completed ON todos(completed, completed_date);
                CREATE TABLE IF NOT EXISTS inbox_items (
                    id TEXT PRIMARY KEY,
                    raw_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'needs_review', 'failed', 'succeeded')),
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    source_provider TEXT NOT NULL DEFAULT 'local',
                    source_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_provider, source_id)
                );
                CREATE INDEX IF NOT EXISTS idx_inbox_updated ON inbox_items(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_items(status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS organizer_reviews (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    month TEXT,
                    status TEXT NOT NULL,
                    total_batches INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS organizer_review_batches (
                    review_id TEXT NOT NULL,
                    batch_number INTEGER NOT NULL,
                    records_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    suggestions_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (review_id, batch_number)
                );
                CREATE INDEX IF NOT EXISTS idx_organizer_reviews_updated ON organizer_reviews(updated_at DESC);
                CREATE TABLE IF NOT EXISTS organizer_change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id TEXT,
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    entry_date TEXT NOT NULL,
                    field TEXT NOT NULL,
                    before_value TEXT NOT NULL,
                    after_value TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_organizer_change_log_applied ON organizer_change_log(applied_at DESC, id DESC);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(transactions)")}
            if "budget_excluded" not in columns:
                connection.execute(
                    "ALTER TABLE transactions ADD COLUMN budget_excluded INTEGER NOT NULL DEFAULT 0"
                )
            todo_columns = {row["name"] for row in connection.execute("PRAGMA table_info(todos)")}
            if "due_date" not in todo_columns:
                connection.execute("ALTER TABLE todos ADD COLUMN due_date TEXT")
                if "reminder_date" in todo_columns:
                    connection.execute("UPDATE todos SET due_date=reminder_date WHERE due_date IS NULL")
            connection.execute(
                "INSERT OR IGNORE INTO preferences(key,value,updated_at) VALUES('monthly_budget',?,?)",
                (f"{DEFAULT_MONTHLY_BUDGET:.2f}", _now()),
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            for row in connection.execute("SELECT DISTINCT account FROM transactions").fetchall():
                self._ensure_category(connection, row["account"])
            connection.commit()

    def is_initialized(self) -> bool:
        with self.session() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='data_initialized'").fetchone()
            return bool(row and row["value"] == "1")

    def initialize_empty(self) -> None:
        """Mark a new profile initialized without importing bundled example data."""
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('data_initialized','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )

    def get_monthly_budget(self) -> float:
        with self.session() as connection:
            row = connection.execute(
                "SELECT value FROM preferences WHERE key='monthly_budget'"
            ).fetchone()
        try:
            return float(Decimal(row["value"] if row else str(DEFAULT_MONTHLY_BUDGET)))
        except (InvalidOperation, ValueError):
            return float(DEFAULT_MONTHLY_BUDGET)

    def set_monthly_budget(self, value: object) -> float:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValidationError("月度预算格式无效。") from error
        if not amount.is_finite() or amount < 0 or amount > Decimal("999999999.99"):
            raise ValidationError("月度预算必须是有效的非负金额。")
        normalized = f"{amount:.2f}"
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO preferences(key,value,updated_at) VALUES('monthly_budget',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (normalized, _now()),
            )
        return float(amount)

    def import_text_data(self, root: Path) -> bool:
        """Seed an empty database from legacy text data exactly once."""
        if self.is_initialized():
            return False
        root = Path(root)
        transactions = LedgerRepository(root).list()
        diary = DiaryRepository(root).list()
        todos = TodoRepository(root).list(include_completed=True)
        events = CalendarRepository(root).list()
        timestamp = _now()
        with self.transaction() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='data_initialized'").fetchone()
            if row and row["value"] == "1":
                return False
            for item in transactions:
                connection.execute(
                    "INSERT OR IGNORE INTO transactions(id,entry_date,summary,note,amount,account,budget_excluded,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["date"], item["summary"], item["note"], f"{item['amount']:.2f}",
                     item["account"], int(item.get("budget_excluded", False)), timestamp, timestamp),
                )
                self._set_mapping(connection, "transaction", item["id"], item["id"])
                self._ensure_category(connection, item["account"])
            for item in diary:
                connection.execute(
                    "INSERT OR IGNORE INTO diary_entries(id,entry_date,entry_time,text,tags_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (item["id"], item["date"], item["time"], item["text"],
                     json.dumps(item["tags"], ensure_ascii=False), timestamp, timestamp),
                )
                self._set_mapping(connection, "diary", item["id"], item["id"])
            for item in todos:
                connection.execute(
                    "INSERT OR IGNORE INTO todos(id,created_date,due_date,text,tags_json,completed,completed_date,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["date"], item.get("dueDate"), item["text"], json.dumps(item["tags"], ensure_ascii=False),
                     int(item["completed"]), item["completedDate"], timestamp, timestamp),
                )
                self._set_mapping(connection, "todo", item["id"], item["id"])
            for item in events:
                start_time = None if item["allDay"] else item["start"][11:16]
                end_time = None if item["allDay"] or not item["end"] else item["end"][11:16]
                connection.execute(
                    "INSERT OR IGNORE INTO events(id,uid,title,event_date,start_time,end_time,all_day,location,description,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["uid"], item["title"], item["date"], start_time, end_time,
                     int(item["allDay"]), item["location"], item["description"], timestamp, timestamp),
                )
                self._set_mapping(connection, "event", item["id"], item["id"])
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('data_initialized','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
        return True

    @staticmethod
    def _new_id(kind: str) -> str:
        return f"{kind}-{uuid.uuid4().hex}"

    @staticmethod
    def _set_mapping(connection: sqlite3.Connection, kind: str, entity_id: str, source_id: str) -> None:
        connection.execute(
            "INSERT INTO projection_map(entity_kind,entity_id,source_id) VALUES(?,?,?) "
            "ON CONFLICT(entity_kind,entity_id) DO UPDATE SET source_id=excluded.source_id",
            (kind, entity_id, source_id),
        )

    @staticmethod
    def _enqueue(connection: sqlite3.Connection, kind: str, entity_id: str, action: str, payload: dict) -> None:
        connection.execute(
            "INSERT INTO outbox(entity_kind,entity_id,action,payload_json,created_at) VALUES(?,?,?,?,?)",
            (kind, entity_id, action, json.dumps(payload, ensure_ascii=False), _now()),
        )

    @staticmethod
    def _ensure_category(connection: sqlite3.Connection, account: str) -> None:
        if account == "expenses":
            return
        parts = account.removeprefix("expenses:").split(":")
        for index in range(1, len(parts) + 1):
            name = ":".join(parts[:index])
            connection.execute(
                "INSERT OR IGNORE INTO categories(name,created_at) VALUES(?,?)", (name, _now())
            )

    def _find_row(self, connection: sqlite3.Connection, kind: str, identifier: str) -> sqlite3.Row:
        queries = {
            "transaction": "SELECT * FROM transactions WHERE id=?",
            "diary": "SELECT * FROM diary_entries WHERE id=?",
            "todo": "SELECT * FROM todos WHERE id=?",
            "event": "SELECT * FROM events WHERE id=?",
        }
        if kind not in queries:
            raise ValidationError("未知的数据类型")
        row = connection.execute(queries[kind], (identifier,)).fetchone()
        if row is None:
            raise NotFoundError("记录不存在或已经发生变化，请刷新后重试。")
        return row

    @staticmethod
    def _inbox_public(row: sqlite3.Row) -> dict:
        try:
            plan = json.loads(row["plan_json"])
        except (TypeError, json.JSONDecodeError):
            plan = {}
        return {
            "id": row["id"],
            "text": row["raw_text"],
            "status": row["status"],
            "plan": plan,
            "error": row["last_error"],
            "attempts": row["attempts"],
            "sourceProvider": row["source_provider"],
            "sourceId": row["source_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def create_inbox_item(
        self,
        raw_text: object,
        *,
        source_provider: str = "local",
        source_id: str | None = None,
    ) -> dict:
        text = str(raw_text or "").strip()
        if not text:
            raise ValidationError("Inbox 内容不能为空。")
        if len(text) > 20_000:
            raise ValidationError("Inbox 内容不能超过 20000 个字符。")
        provider = str(source_provider or "local").strip()[:120] or "local"
        source = str(source_id or "").strip()[:500] or None
        with self.transaction() as connection:
            if source is not None:
                existing = connection.execute(
                    "SELECT * FROM inbox_items WHERE source_provider=? AND source_id=?",
                    (provider, source),
                ).fetchone()
                if existing:
                    return self._inbox_public(existing)
            identifier = self._new_id("inbox")
            stamp = _now()
            connection.execute(
                "INSERT INTO inbox_items(id,raw_text,status,source_provider,source_id,created_at,updated_at) "
                "VALUES(?,?, 'pending', ?, ?, ?, ?)",
                (identifier, text, provider, source, stamp, stamp),
            )
            row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (identifier,)).fetchone()
        return self._inbox_public(row)

    def get_inbox_item(self, identifier: str) -> dict:
        with self.session() as connection:
            row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise NotFoundError("Inbox 项目不存在或已经删除。")
        return self._inbox_public(row)

    def get_inbox_item_by_source(self, source_provider: object, source_id: object) -> dict | None:
        """Find a remote Inbox item without creating a duplicate."""
        provider = str(source_provider or "").strip()[:120]
        source = str(source_id or "").strip()[:500]
        if not provider or not source:
            return None
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM inbox_items WHERE source_provider=? AND source_id=?",
                (provider, source),
            ).fetchone()
        return self._inbox_public(row) if row is not None else None

    def list_inbox_items(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self.session() as connection:
            rows = connection.execute(
                "SELECT * FROM inbox_items ORDER BY updated_at DESC, created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._inbox_public(row) for row in rows]

    def claim_inbox_item(self, identifier: str) -> dict:
        stamp = _now()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise NotFoundError("Inbox 项目不存在或已经删除。")
            if row["status"] == "succeeded":
                return self._inbox_public(row)
            if row["status"] == "processing":
                raise ConflictError("这个 Inbox 项目正在处理中，请稍候。")
            connection.execute(
                "UPDATE inbox_items SET status='processing',attempts=attempts+1,last_error=NULL,updated_at=? WHERE id=?",
                (stamp, identifier),
            )
            row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (identifier,)).fetchone()
        return self._inbox_public(row)

    def fail_inbox_item(self, identifier: str, error: object) -> dict:
        message = str(error or "Inbox 处理失败。")[:2_000]
        with self.transaction() as connection:
            row = connection.execute("SELECT id FROM inbox_items WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise NotFoundError("Inbox 项目不存在或已经删除。")
            connection.execute(
                "UPDATE inbox_items SET status='failed',last_error=?,updated_at=? WHERE id=?",
                (message, _now(), identifier),
            )
            row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (identifier,)).fetchone()
        return self._inbox_public(row)

    def apply_inbox_plan(
        self, identifier: str, raw_plan: object, today: str | None = None
    ) -> tuple[dict, list[str], str]:
        today = today or date.today().isoformat()
        plan = normalize_plan(raw_plan, today)
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise NotFoundError("Inbox 项目不存在或已经删除。")
            if row["status"] == "succeeded":
                return plan, [], "succeeded"
            serialized = json.dumps(plan, ensure_ascii=False)
            if plan["clarifications"]:
                connection.execute(
                    "UPDATE inbox_items SET status='needs_review',plan_json=?,last_error=?,updated_at=? WHERE id=?",
                    (serialized, "；".join(plan["clarifications"])[:2_000], _now(), identifier),
                )
                return plan, [], "needs_review"
            if not any(plan[key] for key in ("transactions", "journal", "todos", "calendar")):
                raise ValidationError("没有收到可写入的内容。")
            warnings: list[str] = []
            self._apply_plan_connection(connection, plan, today, warnings)
            connection.execute(
                "UPDATE inbox_items SET status='succeeded',plan_json=?,last_error=NULL,updated_at=? WHERE id=?",
                (serialized, _now(), identifier),
            )
        return plan, warnings, "succeeded"

    def apply_plan(self, raw_plan: object, today: str | None = None) -> tuple[dict, list[str]]:
        today = today or date.today().isoformat()
        plan = normalize_plan(raw_plan, today)
        warnings: list[str] = []
        with self.transaction() as connection:
            self._apply_plan_connection(connection, plan, today, warnings)
        return plan, warnings

    def _apply_plan_connection(
        self, connection: sqlite3.Connection, plan: dict, today: str, warnings: list[str]
    ) -> None:
        for item in plan["transactions"]:
            existing = connection.execute(
                "SELECT id FROM transactions WHERE entry_date=? AND summary=? AND amount=? AND account=?",
                (item["date"], item["summary"], item["amount"], item["account"]),
            ).fetchone()
            if existing:
                continue
            identifier = self._new_id("transaction")
            stamp = _now()
            connection.execute(
                "INSERT INTO transactions(id,entry_date,summary,note,amount,account,budget_excluded,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (identifier, item["date"], item["summary"], item.get("note", ""), item["amount"],
                 item["account"], int(item.get("budget_excluded", False)), stamp, stamp),
            )
            self._ensure_category(connection, item["account"])
            self._enqueue(connection, "transaction", identifier, "create", item)
        for item in plan["journal"]:
            existing = connection.execute(
                "SELECT id FROM diary_entries WHERE entry_date=? AND text=?", (item["date"], item["text"])
            ).fetchone()
            if existing:
                continue
            identifier = self._new_id("diary")
            stamp = _now()
            payload = {**item, "time": "09:00:00 AM"}
            connection.execute(
                "INSERT INTO diary_entries(id,entry_date,entry_time,text,tags_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (identifier, item["date"], payload["time"], item["text"],
                 json.dumps(item["tags"], ensure_ascii=False), stamp, stamp),
            )
            self._enqueue(connection, "diary", identifier, "create", payload)
        for item in plan["todos"]:
            action = item.get("action")
            existing = connection.execute(
                "SELECT * FROM todos WHERE created_date=? AND text=? AND completed=0 ORDER BY created_at LIMIT 1",
                (item["created_date"], item["text"]),
            ).fetchone()
            if action in {"done", "delete", "restore"}:
                if action == "restore":
                    existing = connection.execute(
                        "SELECT * FROM todos WHERE created_date=? AND text=? AND completed=1 ORDER BY completed_date DESC LIMIT 1",
                        (item["created_date"], item["text"]),
                    ).fetchone()
                if not existing:
                    warnings.append(f"待办未找到，未执行{action}: {item['text']}")
                elif action == "done":
                    self._complete_todo(connection, existing["id"], today)
                elif action == "restore":
                    self._restore_todo(connection, existing["id"])
                else:
                    self._delete(connection, "todo", existing["id"])
                continue
            if existing:
                continue
            identifier = self._new_id("todo")
            stamp = _now()
            connection.execute(
                "INSERT INTO todos(id,created_date,due_date,text,tags_json,completed,completed_date,created_at,updated_at) "
                "VALUES(?,?,?,?,?,0,NULL,?,?)",
                (identifier, item["created_date"], item.get("due_date"), item["text"], json.dumps(item["tags"], ensure_ascii=False),
                 stamp, stamp),
            )
            self._enqueue(connection, "todo", identifier, "create", item)
        for item in plan["calendar"]:
            action = item.get("action")
            source_date = item.get("old_date") if action == "move" else item["date"]
            existing = connection.execute(
                "SELECT * FROM events WHERE event_date=? AND title=? ORDER BY created_at LIMIT 1",
                (source_date, item["title"]),
            ).fetchone()
            if action in {"move", "delete"}:
                if not existing:
                    warnings.append(f"日程未找到，未执行{action}: {item['title']}")
                elif action == "delete":
                    self._delete(connection, "event", existing["id"])
                else:
                    self._update(connection, "event", existing["id"], item)
                continue
            if existing:
                continue
            identifier = self._new_id("event")
            uid = uuid.uuid4().hex.upper()
            stamp = _now()
            connection.execute(
                "INSERT INTO events(id,uid,title,event_date,start_time,end_time,all_day,location,description,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, uid, item["title"], item["date"], item.get("start_time"), item.get("end_time"),
                 int(not item.get("start_time")), item.get("location", ""), item.get("description", ""), stamp, stamp),
            )
            self._enqueue(connection, "event", identifier, "create", {**item, "uid": uid})

    def update(self, kind: str, identifier: str, raw_item: object, today: str | None = None) -> dict:
        item = normalize_item(kind, raw_item, today or date.today().isoformat())
        with self.transaction() as connection:
            self._update(connection, kind, identifier, item)
        return item

    def apply_organizer(
        self,
        transactions: object,
        diary: object,
        todos: object = None,
        *,
        allow_existing: bool = False,
        review_id: str | None = None,
    ) -> dict:
        """Apply category/tag-only organizer changes in one SQLite transaction."""
        if not isinstance(transactions, list) or not isinstance(diary, list):
            raise ValidationError("整理修改格式无效。")
        if todos is None:
            todos = []
        if not isinstance(todos, list):
            raise ValidationError("整理待办格式无效。")
        transaction_updates: list[tuple[str, str]] = []
        diary_updates: list[tuple[str, list[str]]] = []
        todo_updates: list[tuple[str, list[str]]] = []
        seen: set[tuple[str, str]] = set()
        for raw in transactions:
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                raise ValidationError("整理账目格式无效。")
            identifier = str(raw["id"]).strip()
            key = ("transaction", identifier)
            if key in seen:
                raise ValidationError("整理列表中有重复账目。")
            seen.add(key)
            account = str(raw.get("account") or "").strip()
            if not account:
                account = "expenses"
            if account != "expenses" and not ACCOUNT_RE.fullmatch(account):
                raise ValidationError("账目分类格式无效。")
            transaction_updates.append((identifier, account))
        for raw in diary:
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                raise ValidationError("整理日记格式无效。")
            identifier = str(raw["id"]).strip()
            key = ("diary", identifier)
            if key in seen:
                raise ValidationError("整理列表中有重复日记。")
            seen.add(key)
            diary_updates.append((identifier, normalize_tags(raw.get("tags"))))
        for raw in todos:
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                raise ValidationError("整理待办格式无效。")
            identifier = str(raw["id"]).strip()
            key = ("todo", identifier)
            if key in seen:
                raise ValidationError("整理列表中有重复待办。")
            seen.add(key)
            todo_updates.append((identifier, normalize_tags(raw.get("tags"))))

        changed = {"transactions": 0, "diary": 0, "todos": 0}
        with self.transaction() as connection:
            transaction_rows: list[tuple[sqlite3.Row, str]] = []
            for identifier, account in transaction_updates:
                row = self._find_row(connection, "transaction", identifier)
                if not allow_existing and row["account"] != "expenses":
                    raise ConflictError("有账目已经完成分类，请刷新整理页后重试。")
                transaction_rows.append((row, account))
            diary_rows: list[tuple[sqlite3.Row, list[str]]] = []
            for identifier, tags in diary_updates:
                row = self._find_row(connection, "diary", identifier)
                if not allow_existing and _tags(row["tags_json"]):
                    raise ConflictError("有日记已经添加标签，请刷新整理页后重试。")
                diary_rows.append((row, tags))
            todo_rows: list[tuple[sqlite3.Row, list[str]]] = []
            for identifier, tags in todo_updates:
                row = self._find_row(connection, "todo", identifier)
                if not allow_existing and _tags(row["tags_json"]):
                    raise ConflictError("有待办已经添加标签，请刷新整理页后重试。")
                todo_rows.append((row, tags))

            for row, account in transaction_rows:
                stamp = _now()
                if review_id and row["account"] != account:
                    connection.execute(
                        "INSERT INTO organizer_change_log(review_id,entity_kind,entity_id,label,entry_date,field,before_value,after_value,applied_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (review_id, "transaction", row["id"], row["summary"], row["entry_date"], "分类",
                         row["account"], account, stamp),
                    )
                connection.execute(
                    "UPDATE transactions SET account=?,updated_at=? WHERE id=?",
                    (account, stamp, row["id"]),
                )
                self._ensure_category(connection, account)
                self._enqueue(connection, "transaction", row["id"], "update", {
                    "date": row["entry_date"], "summary": row["summary"], "note": row["note"],
                    "amount": row["amount"], "account": account,
                    "budget_excluded": bool(row["budget_excluded"]),
                })
                changed["transactions"] += 1
            for row, tags in diary_rows:
                stamp = _now()
                before_tags = _tags(row["tags_json"])
                if review_id and before_tags != tags:
                    connection.execute(
                        "INSERT INTO organizer_change_log(review_id,entity_kind,entity_id,label,entry_date,field,before_value,after_value,applied_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (review_id, "diary", row["id"], row["text"][:80], row["entry_date"], "标签",
                         json.dumps(before_tags, ensure_ascii=False), json.dumps(tags, ensure_ascii=False), stamp),
                    )
                connection.execute(
                    "UPDATE diary_entries SET tags_json=?,updated_at=? WHERE id=?",
                    (json.dumps(tags, ensure_ascii=False), stamp, row["id"]),
                )
                self._enqueue(connection, "diary", row["id"], "update", {
                    "date": row["entry_date"], "time": row["entry_time"], "text": row["text"], "tags": tags,
                })
                changed["diary"] += 1
            for row, tags in todo_rows:
                stamp = _now()
                before_tags = _tags(row["tags_json"])
                if review_id and before_tags != tags:
                    connection.execute(
                        "INSERT INTO organizer_change_log(review_id,entity_kind,entity_id,label,entry_date,field,before_value,after_value,applied_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (review_id, "todo", row["id"], row["text"][:80], row["created_date"], "标签",
                         json.dumps(before_tags, ensure_ascii=False), json.dumps(tags, ensure_ascii=False), stamp),
                    )
                connection.execute(
                    "UPDATE todos SET tags_json=?,updated_at=? WHERE id=?",
                    (json.dumps(tags, ensure_ascii=False), stamp, row["id"]),
                )
                self._enqueue(connection, "todo", row["id"], "update", {
                    "created_date": row["created_date"], "due_date": row["due_date"],
                    "text": row["text"], "tags": tags,
                })
                changed["todos"] += 1
        return changed

    def create_organizer_review(self, scope: str, month: str | None, batches: list[dict]) -> str:
        if not batches:
            raise ValidationError("没有可供 AI 复核的记录。")
        review_id = self._new_id("review")
        stamp = _now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO organizer_reviews(id,scope,month,status,total_batches,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (review_id, scope, month, "pending", len(batches), stamp, stamp),
            )
            for number, records in enumerate(batches, 1):
                connection.execute(
                    "INSERT INTO organizer_review_batches(review_id,batch_number,records_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (review_id, number, json.dumps(records, ensure_ascii=False), "pending", stamp, stamp),
                )
        return review_id

    @staticmethod
    def _organizer_suggestions(value: str) -> dict:
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {"transactions": [], "diary": [], "todos": []}
        return parsed if isinstance(parsed, dict) else {"transactions": [], "diary": [], "todos": []}

    def organizer_review(self, review_id: str) -> dict:
        with self.session() as connection:
            review = connection.execute("SELECT * FROM organizer_reviews WHERE id=?", (review_id,)).fetchone()
            if review is None:
                raise NotFoundError("复核批次不存在或已经过期。")
            batches = connection.execute(
                "SELECT * FROM organizer_review_batches WHERE review_id=? ORDER BY batch_number", (review_id,)
            ).fetchall()
        batch_data = [{
            "number": row["batch_number"], "status": row["status"], "attempts": row["attempts"],
            "error": row["last_error"], "suggestions": self._organizer_suggestions(row["suggestions_json"]),
        } for row in batches]
        suggestions = {"transactions": [], "diary": [], "todos": []}
        for batch in batch_data:
            for key in suggestions:
                suggestions[key].extend(batch["suggestions"].get(key, []))
        completed = sum(batch["status"] == "completed" for batch in batch_data)
        failed = sum(batch["status"] == "failed" for batch in batch_data)
        return {
            "id": review["id"], "scope": review["scope"], "month": review["month"],
            "status": review["status"], "totalBatches": len(batch_data),
            "completedBatches": completed, "failedBatches": failed,
            "progress": completed / len(batch_data) if batch_data else 0,
            "lastError": review["last_error"], "suggestions": suggestions, "batches": batch_data,
        }

    def claim_organizer_batch(self, review_id: str) -> dict | None:
        stamp = _now()
        with self.transaction() as connection:
            review = connection.execute("SELECT * FROM organizer_reviews WHERE id=?", (review_id,)).fetchone()
            if review is None:
                raise NotFoundError("复核批次不存在或已经过期。")
            row = connection.execute(
                "SELECT * FROM organizer_review_batches WHERE review_id=? AND status='pending' ORDER BY batch_number LIMIT 1",
                (review_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE organizer_review_batches SET status='running',attempts=attempts+1,updated_at=?,last_error=NULL WHERE review_id=? AND batch_number=?",
                (stamp, review_id, row["batch_number"]),
            )
            connection.execute(
                "UPDATE organizer_reviews SET status='running',updated_at=?,last_error=NULL WHERE id=?",
                (stamp, review_id),
            )
        return {"number": row["batch_number"], "records": json.loads(row["records_json"])}

    def complete_organizer_batch(self, review_id: str, batch_number: int, suggestions: dict) -> None:
        stamp = _now()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE organizer_review_batches SET status='completed',suggestions_json=?,last_error=NULL,updated_at=? WHERE review_id=? AND batch_number=?",
                (json.dumps(suggestions, ensure_ascii=False), stamp, review_id, batch_number),
            )
            remaining = connection.execute(
                "SELECT COUNT(*) AS count FROM organizer_review_batches WHERE review_id=? AND status<>'completed'",
                (review_id,),
            ).fetchone()["count"]
            connection.execute(
                "UPDATE organizer_reviews SET status=?,updated_at=?,last_error=NULL WHERE id=?",
                ("completed" if not remaining else "running", stamp, review_id),
            )

    def fail_organizer_batch(self, review_id: str, batch_number: int, error: str) -> None:
        stamp = _now()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE organizer_review_batches SET status='failed',last_error=?,updated_at=? WHERE review_id=? AND batch_number=?",
                (error[:1000], stamp, review_id, batch_number),
            )
            connection.execute(
                "UPDATE organizer_reviews SET status='failed',last_error=?,updated_at=? WHERE id=?",
                (error[:1000], stamp, review_id),
            )

    def retry_organizer_review(self, review_id: str) -> None:
        stamp = _now()
        with self.transaction() as connection:
            review = connection.execute("SELECT id FROM organizer_reviews WHERE id=?", (review_id,)).fetchone()
            if review is None:
                raise NotFoundError("复核批次不存在或已经过期。")
            failed = connection.execute(
                "SELECT COUNT(*) AS count FROM organizer_review_batches WHERE review_id=? AND status='failed'",
                (review_id,),
            ).fetchone()["count"]
            if not failed:
                raise ValidationError("当前没有需要重试的复核批次。")
            connection.execute(
                "UPDATE organizer_review_batches SET status='pending',last_error=NULL,updated_at=? WHERE review_id=? AND status='failed'",
                (stamp, review_id),
            )
            connection.execute(
                "UPDATE organizer_reviews SET status='pending',last_error=NULL,updated_at=? WHERE id=?",
                (stamp, review_id),
            )

    def list_organizer_changes(self, limit: int = 30) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        with self.session() as connection:
            rows = connection.execute(
                "SELECT * FROM organizer_change_log ORDER BY applied_at DESC,id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            before = row["before_value"]
            after = row["after_value"]
            if row["field"] == "标签":
                try:
                    before = json.loads(before)
                except json.JSONDecodeError:
                    before = []
                try:
                    after = json.loads(after)
                except json.JSONDecodeError:
                    after = []
            result.append({
                "id": row["id"], "reviewId": row["review_id"], "kind": row["entity_kind"],
                "entityId": row["entity_id"], "label": row["label"], "date": row["entry_date"],
                "field": row["field"], "before": before, "after": after, "appliedAt": row["applied_at"],
            })
        return result

    def _update(self, connection: sqlite3.Connection, kind: str, identifier: str, item: dict) -> None:
        row = self._find_row(connection, kind, identifier)
        stamp = _now()
        if kind == "transaction":
            connection.execute(
                "UPDATE transactions SET entry_date=?,summary=?,note=?,amount=?,account=?,budget_excluded=?,updated_at=? WHERE id=?",
                (item["date"], item["summary"], item.get("note", ""), item["amount"], item["account"],
                 int(item.get("budget_excluded", False)), stamp, identifier),
            )
            self._ensure_category(connection, item["account"])
        elif kind == "diary":
            item = {**item, "time": row["entry_time"]}
            connection.execute(
                "UPDATE diary_entries SET entry_date=?,text=?,tags_json=?,updated_at=? WHERE id=?",
                (item["date"], item["text"], json.dumps(item["tags"], ensure_ascii=False), stamp, identifier),
            )
        elif kind == "todo":
            if row["completed"]:
                raise ConflictError("已完成待办请先恢复后再编辑。")
            connection.execute(
                "UPDATE todos SET created_date=?,due_date=?,text=?,tags_json=?,updated_at=? WHERE id=?",
                (item["created_date"], item.get("due_date"), item["text"], json.dumps(item["tags"], ensure_ascii=False), stamp, identifier),
            )
        elif kind == "event":
            duplicate = connection.execute(
                "SELECT id FROM events WHERE event_date=? AND title=? AND id<>?",
                (item["date"], item["title"], identifier),
            ).fetchone()
            if duplicate:
                raise ConflictError("目标日期已有同标题日程。")
            connection.execute(
                "UPDATE events SET title=?,event_date=?,start_time=?,end_time=?,all_day=?,location=?,description=?,updated_at=? WHERE id=?",
                (item["title"], item["date"], item.get("start_time"), item.get("end_time"),
                 int(not item.get("start_time")), item.get("location", ""), item.get("description", ""), stamp, identifier),
            )
            item = {**item, "uid": row["uid"]}
        self._enqueue(connection, kind, identifier, "update", item)

    def delete(self, kind: str, identifier: str) -> None:
        with self.transaction() as connection:
            self._delete(connection, kind, identifier)

    def _delete(self, connection: sqlite3.Connection, kind: str, identifier: str) -> None:
        self._find_row(connection, kind, identifier)
        queries = {
            "transaction": "DELETE FROM transactions WHERE id=?",
            "diary": "DELETE FROM diary_entries WHERE id=?",
            "todo": "DELETE FROM todos WHERE id=?",
            "event": "DELETE FROM events WHERE id=?",
        }
        self._enqueue(connection, kind, identifier, "delete", {})
        connection.execute(queries[kind], (identifier,))

    def complete_todo(self, identifier: str, completed_date: str | None = None) -> None:
        with self.transaction() as connection:
            self._complete_todo(connection, identifier, completed_date or date.today().isoformat())

    def _complete_todo(self, connection: sqlite3.Connection, identifier: str, completed_date: str) -> None:
        row = self._find_row(connection, "todo", identifier)
        if row["completed"]:
            raise ConflictError("这项待办已经完成。")
        payload = {"created_date": row["created_date"], "due_date": row["due_date"], "text": row["text"],
                   "tags": _tags(row["tags_json"]), "completed_date": completed_date}
        connection.execute(
            "UPDATE todos SET completed=1,completed_date=?,updated_at=? WHERE id=?",
            (completed_date, _now(), identifier),
        )
        self._enqueue(connection, "todo", identifier, "complete", payload)

    def restore_todo(self, identifier: str) -> None:
        with self.transaction() as connection:
            self._restore_todo(connection, identifier)

    def _restore_todo(self, connection: sqlite3.Connection, identifier: str) -> None:
        row = self._find_row(connection, "todo", identifier)
        if not row["completed"]:
            raise ConflictError("这项待办尚未完成。")
        payload = {"created_date": row["created_date"], "due_date": row["due_date"], "text": row["text"], "tags": _tags(row["tags_json"])}
        connection.execute(
            "UPDATE todos SET completed=0,completed_date=NULL,updated_at=? WHERE id=?", (_now(), identifier)
        )
        self._enqueue(connection, "todo", identifier, "restore", payload)

    def create_category(self, name: object) -> str:
        account = self._category_account(name)
        with self.transaction() as connection:
            self._ensure_category(connection, account)
        return account.removeprefix("expenses:")

    def delete_category(self, name: object, migrate_to: object = None) -> int:
        """Delete a category and move affected transactions or mark them unclassified."""
        old_account = self._category_account(name)
        target_text = str(migrate_to or "").strip()
        new_account = self._category_account(target_text) if target_text else "expenses"
        if old_account == new_account:
            raise ValidationError("迁移目标不能与要删除的分类相同。")
        if new_account.startswith(f"{old_account}:"):
            raise ValidationError("迁移目标不能放在要删除的分类下面。")
        old_name = old_account.removeprefix("expenses:")
        with self.transaction() as connection:
            category = connection.execute("SELECT name FROM categories WHERE name=?", (old_name,)).fetchone()
            if not category:
                raise NotFoundError("分类不存在或已经删除。")
            rows = connection.execute(
                "SELECT * FROM transactions WHERE account=? OR account LIKE ? ORDER BY entry_date,id",
                (old_account, f"{old_account}:%"),
            ).fetchall()
            for row in rows:
                suffix = row["account"][len(old_account):] if new_account != "expenses" else ""
                account = new_account + suffix
                item = {
                    "date": row["entry_date"], "summary": row["summary"], "note": row["note"],
                    "amount": row["amount"], "account": account,
                }
                connection.execute(
                    "UPDATE transactions SET account=?,updated_at=? WHERE id=?",
                    (account, _now(), row["id"]),
                )
                self._enqueue(connection, "transaction", row["id"], "update", item)
                self._ensure_category(connection, account)
            connection.execute(
                "DELETE FROM categories WHERE name=? OR name LIKE ?", (old_name, f"{old_name}:%")
            )
        return len(rows)

    @staticmethod
    def _category_account(value: object) -> str:
        name = str(value or "").strip().replace(" · ", ":").strip(":")
        account = name if name == "expenses" or name.startswith("expenses:") else f"expenses:{name}"
        parts = account.split(":")
        if parts[0] != "expenses" or len(parts) < 2 or any(not part or any(ch.isspace() for ch in part) for part in parts[1:]):
            raise ValidationError("分类不能为空，也不能包含空格或冒号空段。")
        return account

    def list_transactions(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute("SELECT * FROM transactions ORDER BY entry_date DESC, created_at DESC, id DESC").fetchall()
        return [{"id": row["id"], "date": row["entry_date"], "summary": row["summary"], "note": row["note"],
                 "amount": float(row["amount"]), "account": row["account"],
                 "budget_excluded": bool(row["budget_excluded"]),
                 "category": "未分类" if row["account"] == "expenses" else row["account"].removeprefix("expenses:")}
                for row in rows]

    def list_categories(self) -> list[str]:
        with self.session() as connection:
            rows = connection.execute("SELECT name FROM categories ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def list_diary(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute("SELECT * FROM diary_entries ORDER BY entry_date DESC, entry_time DESC, id DESC").fetchall()
        return [{"id": row["id"], "date": row["entry_date"], "time": row["entry_time"],
                 "text": row["text"], "tags": _tags(row["tags_json"])} for row in rows]

    def list_todos(self, include_completed: bool = False) -> list[dict]:
        query = "SELECT * FROM todos" if include_completed else "SELECT * FROM todos WHERE completed=0"
        query += " ORDER BY completed ASC, CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date ASC, created_at DESC, id DESC"
        with self.session() as connection:
            rows = connection.execute(query).fetchall()
        return [{"id": row["id"], "date": row["created_date"], "dueDate": row["due_date"], "text": row["text"],
                 "rawText": (row["text"] + " " + " ".join(f"@{tag}" for tag in _tags(row["tags_json"]))).strip(),
                 "tags": _tags(row["tags_json"]), "completed": bool(row["completed"]),
                 "completedDate": row["completed_date"]} for row in rows]

    def list_events(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY event_date, COALESCE(start_time,''), title").fetchall()
        result = []
        for row in rows:
            start = row["event_date"] if row["all_day"] else f"{row['event_date']}T{row['start_time']}:00+08:00"
            end = "" if row["all_day"] or not row["end_time"] else f"{row['event_date']}T{row['end_time']}:00+08:00"
            result.append({"id": row["id"], "uid": row["uid"], "title": row["title"], "date": row["event_date"],
                           "start": start, "end": end, "allDay": bool(row["all_day"]),
                           "location": row["location"], "description": row["description"]})
        return result

    def snapshot_to(self, target: Path) -> Path:
        """Create a transactionally consistent SQLite backup."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(target)
        try:
            with self.session() as source:
                source.backup(destination)
            destination.commit()
        finally:
            destination.close()
        return target

    def restore_from(self, source_path: Path) -> None:
        """Validate and atomically copy a backup database into the active store."""
        source_path = Path(source_path)
        source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
        try:
            integrity = source.execute("PRAGMA integrity_check").fetchone()
            required = {"meta", "transactions", "diary_entries", "todos", "events", "outbox"}
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not integrity or integrity[0] != "ok" or not required.issubset(tables):
                raise ValidationError("备份数据库无效或已经损坏。")
            destination = sqlite3.connect(self.path)
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
        except sqlite3.Error as error:
            raise ValidationError("备份数据库无效或已经损坏。") from error
        finally:
            source.close()
        self._initialize_schema()

    def mark_backup_pending(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('backup_pending','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )

    def mark_backup_complete(self, target: str, timestamp: str) -> None:
        with self.transaction() as connection:
            for key, value in (("backup_pending", "0"), ("last_backup_target", target), ("last_backup_at", timestamp)):
                connection.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    def backup_state(self) -> dict:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT key,value FROM meta WHERE key IN ('backup_pending','last_backup_target','last_backup_at')"
            ).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        return {
            "pending": values.get("backup_pending", "1") == "1",
            "lastBackupTarget": values.get("last_backup_target"),
            "lastBackupAt": values.get("last_backup_at"),
        }

    def pending_jobs(self, limit: int = 500) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT o.*, p.source_id FROM outbox o LEFT JOIN projection_map p "
                "ON p.entity_kind=o.entity_kind AND p.entity_id=o.entity_id ORDER BY o.id LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def finish_jobs(self, job_ids: list[int], mapping_updates: dict[tuple[str, str], str | None]) -> None:
        if not job_ids:
            return
        with self.transaction() as connection:
            for (kind, entity_id), source_id in mapping_updates.items():
                if source_id is None:
                    connection.execute(
                        "DELETE FROM projection_map WHERE entity_kind=? AND entity_id=?", (kind, entity_id)
                    )
                else:
                    self._set_mapping(connection, kind, entity_id, source_id)
            connection.executemany("DELETE FROM outbox WHERE id=?", [(job_id,) for job_id in job_ids])

    def fail_jobs(self, job_ids: list[int], error: str) -> None:
        if not job_ids:
            return
        with self.transaction() as connection:
            connection.executemany(
                "UPDATE outbox SET attempts=attempts+1,last_error=? WHERE id=?",
                [(error[:1000], job_id) for job_id in job_ids],
            )

    def maintenance_status(self) -> dict:
        with self.session() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, MAX(attempts) AS attempts, MAX(last_error) AS error FROM outbox"
            ).fetchone()
        return {"pending": int(row["count"]), "attempts": int(row["attempts"] or 0), "lastError": row["error"]}
