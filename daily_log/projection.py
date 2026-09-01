"""Project durable SQLite outbox operations into portable text formats."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .database import DailyLogDatabase
from .errors import NotFoundError
from .storage import CalendarRepository, DiaryRepository, LedgerRepository, TodoRepository
from .transaction import data_transaction


def _transaction_id(repository: LedgerRepository, item: dict) -> str:
    expected_amount = f"{float(item['amount']):.2f}"
    for entry in repository.list():
        if (entry["date"], entry["summary"], f"{entry['amount']:.2f}", entry["account"]) == (
            item["date"], item["summary"], expected_amount, item["account"]
        ):
            return entry["id"]
    raise NotFoundError("账目已经写入，但无法重新定位。")


def _diary_id(repository: DiaryRepository, item: dict) -> str:
    for entry in repository.list():
        if entry["date"] == item["date"] and entry["text"] == item["text"]:
            return entry["id"]
    raise NotFoundError("日记已经写入，但无法重新定位。")


def _todo_id(repository: TodoRepository, item: dict, *, completed: bool) -> str:
    expected_tags = item.get("tags", [])
    for entry in repository.list(include_completed=True):
        if (entry["date"] == item["created_date"] and entry["text"] == item["text"]
                and entry["tags"] == expected_tags and entry["completed"] is completed):
            return entry["id"]
    raise NotFoundError("待办已经写入，但无法重新定位。")


def _event_id(repository: CalendarRepository, item: dict) -> str:
    for entry in repository.list():
        if entry["date"] == item["date"] and entry["title"] == item["title"]:
            return entry["id"]
    raise NotFoundError("日程已经写入，但无法重新定位。")


def _apply_job(root: Path, job: dict, source_id: str | None) -> str | None:
    kind = job["entity_kind"]
    action = job["action"]
    item = job["payload"]
    if kind == "transaction":
        repository = LedgerRepository(root)
        if action == "create":
            repository.add(item)
            return _transaction_id(repository, item)
        if not source_id:
            raise NotFoundError("账目缺少文本映射，无法导出。")
        if action == "update":
            repository.update(source_id, item)
            return _transaction_id(repository, item)
        repository.delete(source_id)
        return None
    if kind == "diary":
        repository = DiaryRepository(root)
        if action == "create":
            repository.add(item)
            return _diary_id(repository, item)
        if not source_id:
            raise NotFoundError("日记缺少文本映射，无法导出。")
        if action == "update":
            repository.update(source_id, item)
            return _diary_id(repository, item)
        repository.delete(source_id)
        return None
    if kind == "todo":
        repository = TodoRepository(root)
        if action == "create":
            repository.add(item)
            return _todo_id(repository, item, completed=False)
        if not source_id:
            raise NotFoundError("待办缺少文本映射，无法导出。")
        if action == "update":
            repository.update(source_id, item)
            return _todo_id(repository, item, completed=False)
        if action == "complete":
            repository.complete(source_id, item["completed_date"])
            return _todo_id(repository, item, completed=True)
        if action == "restore":
            repository.restore(source_id)
            return _todo_id(repository, item, completed=False)
        repository.delete(source_id)
        return None
    if kind == "event":
        repository = CalendarRepository(root)
        if action == "create":
            repository.add(item)
            return _event_id(repository, item)
        if not source_id:
            raise NotFoundError("日程缺少文本映射，无法导出。")
        if action == "update":
            repository.update(source_id, item)
            return _event_id(repository, item)
        repository.delete(source_id)
        return None
    raise NotFoundError("未知的后台导出任务。")


def project_pending(
    database: DailyLogDatabase,
    root: Path,
    *,
    prepare: Callable[[], None] | None = None,
) -> int:
    """Apply one ordered outbox batch and remove jobs only after full success."""
    jobs = database.pending_jobs()
    if not jobs:
        return 0
    root = Path(root)
    job_ids = [int(job["id"]) for job in jobs]
    mappings: dict[tuple[str, str], str | None] = {
        (job["entity_kind"], job["entity_id"]): job.get("source_id") for job in jobs
    }
    try:
        with data_transaction(root):
            for job in jobs:
                key = (job["entity_kind"], job["entity_id"])
                mappings[key] = _apply_job(root, job, mappings.get(key))
            if prepare:
                prepare()
        database.finish_jobs(job_ids, mappings)
        return len(jobs)
    except Exception as error:
        database.fail_jobs(job_ids, str(error))
        raise
