#!/usr/bin/env python3
"""Local HTTP server for the daily-log web application."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from web_data import build_dashboard


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daily_log import DailyLogError, normalize_plan  # noqa: E402
from daily_log.database import DailyLogDatabase  # noqa: E402
from daily_log.models import ACCOUNT_RE, normalize_tags  # noqa: E402
from daily_log.data_location import relocate_profile, redirect_path, write_redirect  # noqa: E402
from daily_log.config import LocalConfig  # noqa: E402
from daily_log.paths import AppPaths  # noqa: E402
from daily_log.runtime import bootstrap_runtime, migrate_legacy_runtime  # noqa: E402
from daily_log.ai import parse_with_ai, suggest_organizer_with_ai, test_ai_connection  # noqa: E402
from daily_log.worker import ProjectionWorker  # noqa: E402
from daily_log.idle_worker import IdleWorker  # noqa: E402
from daily_log.cloud_backup import (  # noqa: E402
    BackupError,
    download_latest_archive,
    test_s3_connection,
    test_webdav_connection,
    upload_archive,
)
from daily_log.exporter import create_portable_archive, export_data_file  # noqa: E402
from daily_log.backup_archive import (  # noqa: E402
    decrypt_archive,
    encrypt_archive,
    is_encrypted_archive,
    restore_backup,
)
from daily_log.version import __version__  # noqa: E402
from daily_log.calendar_subscription import (  # noqa: E402
    SubscriptionError,
    delete_subscription_cache,
    load_subscription_events,
    refresh_subscription,
    subscription_cache_path,
    subscription_status,
)
from daily_log.diagnostics import close_logging, configure_logging  # noqa: E402
from daily_log.single_instance import InstanceAlreadyRunning, SingleInstance  # noqa: E402
WEB_ROOT = ROOT / "web"
MAX_REQUEST_SIZE = 64 * 1024
BACKUP_LOCK = threading.Lock()
LAST_BACKUP_ERROR: str | None = None
LAST_BACKUP_AT: str | None = None
LAST_BACKUP_TARGET: str | None = None
BACKUP_PENDING = False
DATABASE: DailyLogDatabase | None = None
WORKER: ProjectionWorker | None = None
AUTO_BACKUP: IdleWorker | None = None
PATHS = AppPaths.default()
CONFIG = LocalConfig(PATHS.config)
LEGACY_MIGRATION_SOURCE: Path | None = None
DATA_DIR_EXPLICIT = False
ITEM_ROUTE = re.compile(r"^/api/items/(transaction|diary|todo|event)/([A-Za-z0-9-]+)$")
TODO_ACTION_ROUTE = re.compile(r"^/api/todos/([A-Za-z0-9-]+)/(complete|restore)$")
INBOX_ROUTE = re.compile(r"^/api/inbox/([A-Za-z0-9-]+)(?:/(process|apply))?$")
ORGANIZER_ID = re.compile(r"^[A-Za-z0-9-]+$")
ORGANIZER_REVIEW_ROUTE = re.compile(r"^/api/organize/reviews/([A-Za-z0-9-]+)(?:/(retry|next))?$")
ORGANIZER_BATCH_SIZE = 20
LOGGER = logging.getLogger("daily_log.web")


class WebError(RuntimeError):
    """An expected error that is safe to show in the local UI."""


def initialize_runtime(database: DailyLogDatabase | None = None) -> tuple[DailyLogDatabase, ProjectionWorker]:
    global DATABASE, WORKER, AUTO_BACKUP
    if DATABASE is not None and WORKER is not None:
        return DATABASE, WORKER
    DATABASE = database or DailyLogDatabase(PATHS.database)
    if LEGACY_MIGRATION_SOURCE is None:
        bootstrap_runtime(DATABASE, PATHS)
    else:
        migrate_legacy_runtime(DATABASE, PATHS, LEGACY_MIGRATION_SOURCE)
    WORKER = ProjectionWorker(DATABASE, PATHS.portable_root)
    WORKER.start()
    AUTO_BACKUP = IdleWorker(
        lambda: backup_now("闲置自动备份"),
        lambda: (
            CONFIG.public()["backup"]["autoBackup"],
            CONFIG.public()["backup"]["idleSeconds"],
        ),
    )
    AUTO_BACKUP.start()
    ensure_subscription_refresh()
    return DATABASE, WORKER


def configure_runtime(
    paths: AppPaths,
    legacy_migration_source: Path | None = None,
    *,
    explicit_data_dir: bool | None = None,
) -> None:
    """Select writable application state before the server starts."""
    global PATHS, CONFIG, LEGACY_MIGRATION_SOURCE, DATA_DIR_EXPLICIT
    if DATABASE is not None or WORKER is not None:
        raise WebError("应用已经启动，不能再更换数据目录。")
    PATHS = paths
    PATHS.ensure()
    CONFIG = LocalConfig(PATHS.config)
    LEGACY_MIGRATION_SOURCE = legacy_migration_source
    DATA_DIR_EXPLICIT = bool(os.environ.get("DAILY_LOG_STATE_DIR")) if explicit_data_dir is None else explicit_data_dir
    configure_logging(PATHS.logs)


def _refresh_subscription_quietly(item: dict) -> None:
    try:
        refresh_subscription(item["url"], subscription_cache_path(item["id"]))
    except (DailyLogError, SubscriptionError):
        return


def ensure_subscription_refresh(force: bool = False) -> None:
    for item in CONFIG.calendar_subscriptions():
        if not item["enabled"]:
            continue
        cache = subscription_cache_path(item["id"])
        stale = not cache.exists() or time.time() - cache.stat().st_mtime > 24 * 60 * 60
        if force or stale:
            threading.Thread(
                target=_refresh_subscription_quietly,
                args=(item,),
                name=f"daily-log-calendar-{item['id']}",
                daemon=True,
            ).start()


def subscribed_events() -> list[dict]:
    events = []
    for item in CONFIG.calendar_subscriptions():
        if not item["enabled"]:
            continue
        try:
            events.extend(load_subscription_events(
                subscription_cache_path(item["id"]),
                subscription_id=item["id"],
                subscription_name=item["name"],
            ))
        except SubscriptionError:
            continue
    return events


def settings_public() -> dict:
    result = CONFIG.public()
    result["about"] = {"name": "Daily Log", "version": __version__, "license": "MIT"}
    result["finance"] = {"monthlyBudget": database().get_monthly_budget()}
    result["dataPath"] = str(PATHS.state_dir)
    for item in result["calendarSubscriptions"]:
        item.update(subscription_status(subscription_cache_path(item["id"])))
    return result


def update_settings(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("设置格式无效。")
    result = CONFIG.update(payload)
    finance = payload.get("finance", {})
    if finance:
        if not isinstance(finance, dict):
            raise WebError("预算设置格式无效。")
        database().set_monthly_budget(finance.get("monthlyBudget"))
        database().mark_backup_pending()
    result["finance"] = {"monthlyBudget": database().get_monthly_budget()}
    result["dataPath"] = str(PATHS.state_dir)
    return result


def relocate_data_directory(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("数据目录设置格式无效。")
    if DATA_DIR_EXPLICIT:
        raise WebError("当前启动使用了命令行或环境变量指定的数据目录，请修改启动参数后再迁移。")
    raw_target = str(payload.get("path") or "").strip()
    if not raw_target:
        raise WebError("请填写新的数据目录。")
    worker().flush()
    safety = create_portable_archive(
        database(),
        destination=PATHS.restore_safety,
        include_database=True,
        settings_text=CONFIG.portable_text(),
        portable_root=PATHS.portable_root,
        secrets_text=json.dumps(CONFIG.secrets(), ensure_ascii=False, indent=2),
    )
    program_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
    details = relocate_profile(database(), PATHS.state_dir, Path(raw_target), program_root=program_root)
    write_redirect(Path(raw_target).expanduser().resolve())
    details.update({
        "message": "数据目录已准备完成，请重启 Daily Log 后生效。",
        "safetyBackup": str(safety),
        "redirect": str(redirect_path()),
    })
    return details


def complete_onboarding(payload: object) -> dict:
    if payload is not None and not isinstance(payload, dict):
        raise WebError("首次使用设置格式无效。")
    payload = payload or {}
    budget = payload.get("monthlyBudget")
    if budget is not None and str(budget).strip():
        database().set_monthly_budget(budget)
    result = CONFIG.complete_onboarding()
    result["finance"] = {"monthlyBudget": database().get_monthly_budget()}
    result["dataPath"] = str(PATHS.state_dir)
    database().mark_backup_pending()
    return result


def _candidate_ai_settings(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("AI 测试配置格式无效。")
    current = CONFIG.ai_credentials()
    for source, target in (("baseUrl", "base_url"), ("model", "model")):
        if source in payload:
            current[target] = str(payload[source] or "").strip()
    if str(payload.get("apiKey", "")).strip():
        current["api_key"] = str(payload["apiKey"]).strip()
    current["enabled"] = True
    return current


def test_ai(payload: object) -> dict:
    return test_ai_connection(_candidate_ai_settings(payload))


def _candidate_backup_settings(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("备份测试配置格式无效。")
    current = CONFIG.backup_settings()
    backend = str(payload.get("backend", current["backend"])).strip().lower()
    current["backend"] = backend
    for section_name, fields in (("webdav", ("url", "username", "allow_private")), ("s3", ("endpoint", "region", "bucket", "prefix", "allow_private"))):
        incoming = payload.get(section_name, {})
        if incoming is not None and not isinstance(incoming, dict):
            raise WebError("备份测试配置格式无效。")
        incoming = incoming or {}
        section = current[section_name]
        for field in fields:
            if field in incoming:
                section[field] = str(incoming[field] or "").strip()
        for source, target in (("accessKey", "access_key"), ("secretKey", "secret_key"), ("allowPrivate", "allow_private")):
            if source in incoming:
                section[target] = str(incoming[source] or "").strip()
        if "password" in incoming and str(incoming["password"] or "").strip():
            section["password"] = str(incoming["password"]).strip()
    incoming_proxy = payload.get("proxy", {})
    if incoming_proxy is not None and not isinstance(incoming_proxy, dict):
        raise WebError("代理测试配置格式无效。")
    incoming_proxy = incoming_proxy or {}
    proxy = current["proxy"]
    for field in ("mode", "url", "username"):
        if field in incoming_proxy:
            proxy[field] = str(incoming_proxy[field] or "").strip()
    if "password" in incoming_proxy and str(incoming_proxy["password"] or "").strip():
        proxy["password"] = str(incoming_proxy["password"]).strip()
    return current


def test_backup(payload: object) -> dict:
    settings = _candidate_backup_settings(payload)
    if settings["backend"] == "local":
        PATHS.backups.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "message": "本机备份目录可以正常使用。"}
    if settings["backend"] == "webdav":
        return test_webdav_connection({**settings["webdav"], "proxy": settings["proxy"]})
    if settings["backend"] == "s3":
        return test_s3_connection({**settings["s3"], "proxy": settings["proxy"]})
    raise WebError("暂不支持这个备份方式。")


def summarize_plan(plan: dict, warnings: list[str]) -> str:
    parts = []
    labels = (("transactions", "账目"), ("journal", "日记"), ("todos", "待办"), ("calendar", "日程"))
    for key, label in labels:
        if plan.get(key):
            parts.append(f"{label} {len(plan[key])} 条")
    if warnings:
        parts.append(f"提醒 {len(warnings)} 条")
    return "，".join(parts) or "没有新增内容"


def database() -> DailyLogDatabase:
    return initialize_runtime()[0]


def worker() -> ProjectionWorker:
    return initialize_runtime()[1]


def apply_plan(raw_plan: object) -> dict:
    today = datetime.now().date().isoformat()
    plan = normalize_plan(raw_plan, today)
    if plan["clarifications"]:
        raise WebError("；".join(plan["clarifications"]))
    if not any(plan[key] for key in ("transactions", "journal", "todos", "calendar")):
        raise WebError("没有收到可写入的内容。")

    plan, warnings = database().apply_plan(plan, today)
    worker().notify()
    if AUTO_BACKUP is not None:
        AUTO_BACKUP.notify()
    database().mark_backup_pending()
    return {
        "message": "已保存到本地，正在后台整理",
        "warnings": warnings,
        "summary": summarize_plan(plan, warnings),
        "maintenance": worker().status(),
    }


def _inbox_backup_pending() -> None:
    if AUTO_BACKUP is not None:
        AUTO_BACKUP.notify()
    database().mark_backup_pending()


def _inbox_local_save() -> None:
    worker().notify()
    _inbox_backup_pending()


def process_inbox_item(identifier: str) -> dict:
    claimed = database().claim_inbox_item(identifier)
    if claimed["status"] == "succeeded":
        return {"item": claimed, "status": "succeeded", "message": "这个 Inbox 项目已经入库。"}
    try:
        parsed = parse_ai(claimed["text"])
        plan, warnings, status = database().apply_inbox_plan(identifier, parsed["plan"])
    except Exception as error:  # noqa: BLE001
        item = database().fail_inbox_item(identifier, error)
        _inbox_backup_pending()
        return {"item": item, "status": "failed", "message": str(error)}
    item = database().get_inbox_item(identifier)
    result = {
        "item": item,
        "status": status,
        "warnings": warnings,
        "summary": summarize_plan(plan, warnings),
        "message": "AI 已整理并写入本地数据库" if status == "succeeded" else "AI 已整理，请确认需要补充的内容",
    }
    if status == "succeeded":
        _inbox_local_save()
    else:
        _inbox_backup_pending()
    return result


def create_inbox(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("Inbox 格式无效。")
    item = database().create_inbox_item(
        payload.get("text", ""),
        source_provider=str(payload.get("sourceProvider") or "local"),
        source_id=payload.get("sourceId"),
    )
    if item["status"] in {"pending", "failed"}:
        result = process_inbox_item(item["id"])
    else:
        result = {"item": item, "status": item["status"], "message": "这个 Inbox 项目已经处理过。"}
    return result


def apply_inbox_item(identifier: str, payload: object) -> dict:
    if not isinstance(payload, dict) or "plan" not in payload:
        raise WebError("Inbox 结果格式无效。")
    plan, warnings, status = database().apply_inbox_plan(identifier, payload["plan"])
    item = database().get_inbox_item(identifier)
    if status == "succeeded":
        _inbox_local_save()
    else:
        _inbox_backup_pending()
    return {
        "item": item,
        "status": status,
        "warnings": warnings,
        "summary": summarize_plan(plan, warnings),
        "message": "Inbox 内容已写入本地数据库" if status == "succeeded" else "仍有内容需要确认",
    }


def get_backup_status() -> dict:
    maintenance = worker().status()
    configured = CONFIG.public()["backup"]
    stored = database().backup_state()
    status = {
        "state": "error" if LAST_BACKUP_ERROR else "pending" if stored["pending"] else "synced",
        "pending": stored["pending"] or bool(maintenance["pending"]),
        "busy": BACKUP_LOCK.locked(),
        "maintenance": maintenance,
        "backend": configured["backend"],
        "lastBackupAt": stored["lastBackupAt"],
        "lastBackupTarget": stored["lastBackupTarget"],
    }
    if AUTO_BACKUP is not None:
        auto_status = AUTO_BACKUP.status()
        status["autoBackup"] = auto_status
        status["busy"] = status["busy"] or auto_status["busy"]
        if auto_status["lastError"] and not status.get("lastError"):
            status["lastError"] = auto_status["lastError"]
    if maintenance["pending"] or maintenance["busy"]:
        status["pending"] = True
        status["state"] = "pending"
    if LAST_BACKUP_ERROR:
        status["lastError"] = LAST_BACKUP_ERROR
    return status


def backup_now(message: object = None, *, timeout: float = 120) -> dict:
    global LAST_BACKUP_ERROR, LAST_BACKUP_AT, LAST_BACKUP_TARGET, BACKUP_PENDING
    if not BACKUP_LOCK.acquire(blocking=False):
        raise WebError("备份仍在处理中，请稍候。")
    try:
        worker().flush()
        settings = CONFIG.backup_settings()
        secrets_text = json.dumps(CONFIG.secrets(), ensure_ascii=False, indent=2)
        archive = create_portable_archive(
            database(),
            destination=PATHS.backups,
            include_database=settings["include_data"],
            settings_text=CONFIG.portable_text() if settings["include_data"] else None,
            include_portable=settings["include_data"],
            portable_root=PATHS.portable_root if settings["include_data"] else None,
            secrets_text=secrets_text,
        )
        if settings["encrypt_backup"]:
            archive = encrypt_archive(archive, settings["encryption_password"], remove_source=True)
        target = upload_archive(archive, settings, timeout=timeout)
        LAST_BACKUP_ERROR = None
        LAST_BACKUP_AT = datetime.now().astimezone().isoformat(timespec="seconds")
        LAST_BACKUP_TARGET = target
        database().mark_backup_complete(target, LAST_BACKUP_AT)
        BACKUP_PENDING = False
        status = get_backup_status()
        status["busy"] = False
        return {"message": "备份完成", "path": str(archive), "status": status}
    except (BackupError, DailyLogError) as error:
        LAST_BACKUP_ERROR = str(error)
        raise WebError(str(error)) from error
    finally:
        BACKUP_LOCK.release()


def restore_latest(password: object = "") -> dict:
    global LAST_BACKUP_ERROR
    if not BACKUP_LOCK.acquire(blocking=False):
        raise WebError("备份或恢复仍在处理中，请稍候。")
    try:
        worker().flush()
        settings = CONFIG.backup_settings()
        archive = download_latest_archive(settings, PATHS.backups)
        safety = create_portable_archive(
            database(),
            destination=PATHS.restore_safety,
            include_database=True,
            settings_text=CONFIG.portable_text(),
            portable_root=PATHS.portable_root,
            secrets_text=json.dumps(CONFIG.secrets(), ensure_ascii=False, indent=2),
        )
        with tempfile.TemporaryDirectory(dir=PATHS.state_dir) as directory:
            working_archive = archive
            if is_encrypted_archive(archive):
                if not str(password or ""):
                    raise WebError("这个备份已加密，请输入备份密码。")
                working_archive = decrypt_archive(
                    archive, str(password), Path(directory) / "daily-log-restored.zip"
                )
            details = restore_backup(
                working_archive, database(), CONFIG, PATHS.portable_root, password=str(password or "")
            )
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        database().mark_backup_complete(str(archive), timestamp)
        LAST_BACKUP_ERROR = None
        return {
            "message": "已从最新备份恢复",
            "source": str(archive),
            "safetyBackup": str(safety),
            "restored": details,
        }
    except (BackupError, DailyLogError, SubscriptionError) as error:
        LAST_BACKUP_ERROR = str(error)
        raise WebError(str(error)) from error
    finally:
        BACKUP_LOCK.release()


def _find_subscription(identifier: object) -> dict:
    for item in CONFIG.calendar_subscriptions():
        if item["id"] == str(identifier):
            return item
    raise WebError("日历订阅不存在。")


def create_calendar_subscription(name: object, url: object) -> dict:
    item = CONFIG.add_calendar_subscription(name, url)
    try:
        result = refresh_subscription(item["url"], subscription_cache_path(item["id"]))
    except (DailyLogError, SubscriptionError):
        CONFIG.delete_calendar_subscription(item["id"])
        delete_subscription_cache(item["id"])
        raise
    return {"message": f"已添加订阅“{item['name']}”", "subscription": item, "refresh": result}


def toggle_calendar_subscription(identifier: object, enabled: object) -> dict:
    item = CONFIG.toggle_calendar_subscription(identifier, enabled)
    if item["enabled"] and not subscription_cache_path(item["id"]).exists():
        try:
            refresh_subscription(item["url"], subscription_cache_path(item["id"]))
        except (DailyLogError, SubscriptionError):
            CONFIG.toggle_calendar_subscription(identifier, False)
            raise
    return {"message": "订阅已显示" if item["enabled"] else "订阅已隐藏", "subscription": item}


def delete_calendar_subscription(identifier: object) -> dict:
    item = _find_subscription(identifier)
    CONFIG.delete_calendar_subscription(identifier)
    delete_subscription_cache(identifier)
    return {"message": f"已删除订阅“{item['name']}”"}


def refresh_calendar_subscription(identifier: object) -> dict:
    item = _find_subscription(identifier)
    return refresh_subscription(item["url"], subscription_cache_path(item["id"]))


def _local_change(action) -> dict:
    action()
    worker().notify()
    if AUTO_BACKUP is not None:
        AUTO_BACKUP.notify()
    database().mark_backup_pending()
    return {"message": "已保存到本地，正在后台整理", "maintenance": worker().status()}


def update_item(kind: str, identifier: str, raw_item: object) -> dict:
    return _local_change(lambda: database().update(kind, identifier, raw_item))


def delete_item(kind: str, identifier: str) -> dict:
    return _local_change(lambda: database().delete(kind, identifier))


def change_todo(identifier: str, action: str) -> dict:
    if action == "complete":
        return _local_change(lambda: database().complete_todo(identifier))
    return _local_change(lambda: database().restore_todo(identifier))


def create_category(name: object) -> dict:
    category = database().create_category(name)
    database().mark_backup_pending()
    if AUTO_BACKUP is not None:
        AUTO_BACKUP.notify()
    return {"message": f"已增加分类“{category}”", "category": category}


def delete_category(name: object, migrate_to: object) -> dict:
    count = database().delete_category(name, migrate_to)
    worker().notify()
    if AUTO_BACKUP is not None:
        AUTO_BACKUP.notify()
    database().mark_backup_pending()
    return {
        "message": f"分类已删除，{count} 笔历史账目已迁移",
        "count": count,
        "maintenance": worker().status(),
    }


def export_data(export_format: object) -> dict:
    worker().flush()
    exported = export_data_file(database(), export_format, destination=PATHS.exports)
    return {"message": "文件已导出", "path": str(exported), "format": str(export_format)}


def parse_ai(text: object) -> dict:
    data = database()
    accounts = sorted({item["account"] for item in data.list_transactions() if item["account"] != "expenses"})
    todos = [f"{item['date']} {item['rawText']}" for item in data.list_todos()]
    plan = parse_with_ai(text, CONFIG.ai_credentials(), context={"accounts": accounts, "todos": todos})
    return {"plan": plan}


def record_with_ai(text: object) -> dict:
    parsed = parse_ai(text)
    result = apply_plan(parsed["plan"])
    result["message"] = "AI 已整理并写入本地数据库"
    return result


def organizer_snapshot(scope: str = "unorganized", month: str | None = None) -> dict:
    if scope not in {"unorganized", "month", "all"}:
        raise WebError("整理范围无效。")
    if scope == "month":
        if not month or not re.fullmatch(r"\d{4}-\d{2}", month):
            raise WebError("整理月份格式无效。")
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError as error:
            raise WebError("整理月份无效。") from error
    data = database()
    all_transactions = data.list_transactions()
    all_diary = data.list_diary()
    all_todos = data.list_todos(include_completed=True)
    in_scope = lambda item: scope != "month" or item["date"].startswith(month or "")
    if scope == "unorganized":
        transactions = [item for item in all_transactions if item["account"] == "expenses"]
        diary = [item for item in all_diary if not item["tags"]]
        todos = [item for item in all_todos if not item["tags"]]
    else:
        transactions = [item for item in all_transactions if in_scope(item)]
        diary = [item for item in all_diary if in_scope(item)]
        todos = [item for item in all_todos if in_scope(item)]
    known_tags = sorted({tag for item in all_diary + all_todos for tag in item["tags"]})
    return {
        "scope": scope,
        "month": month if scope == "month" else None,
        "transactions": transactions,
        "diary": diary,
        "todos": todos,
        "categories": data.list_categories(),
        "knownTags": known_tags,
        "history": data.list_organizer_changes(),
    }


def _selected_organizer_records(payload: dict, snapshot: dict) -> dict:
    def ids(name: str, records: list[dict]) -> set[str]:
        if name not in payload:
            return {item["id"] for item in records}
        values = payload.get(name)
        if not isinstance(values, list):
            raise WebError("整理选择格式无效。")
        result = {str(value).strip() for value in values if str(value).strip()}
        if any(not ORGANIZER_ID.fullmatch(value) for value in result):
            raise WebError("整理记录编号无效。")
        available = {item["id"] for item in records}
        if not result.issubset(available):
            raise WebError("整理记录已经变化，请刷新后重试。")
        return result

    transaction_ids = ids("transactionIds", snapshot["transactions"])
    diary_ids = ids("diaryIds", snapshot["diary"])
    todo_records = snapshot.get("todos", [])
    todo_ids = ids("todoIds", todo_records)
    return {
        "transactions": [item for item in snapshot["transactions"] if item["id"] in transaction_ids],
        "diary": [item for item in snapshot["diary"] if item["id"] in diary_ids],
        "todos": [item for item in todo_records if item["id"] in todo_ids],
    }


def suggest_organizer(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("整理请求格式无效。")
    scope = str(payload.get("scope") or "unorganized")
    month = str(payload.get("month") or "") or None
    snapshot = organizer_snapshot(scope, month)
    records = _selected_organizer_records(payload, snapshot)
    if not any(records.values()):
        raise WebError("请先选择需要整理的记录。")
    suggestions = suggest_organizer_with_ai(
        records,
        CONFIG.ai_credentials(),
        context={"accounts": [f"expenses:{item}" for item in snapshot["categories"]], "tags": snapshot["knownTags"]},
    )
    return _normalize_organizer_suggestions(suggestions, records, snapshot)


def _normalize_organizer_suggestions(suggestions: object, records: dict, snapshot: dict) -> dict:
    if not isinstance(suggestions, dict):
        raise WebError("AI 整理建议格式无效。")
    valid_transactions = {item["id"]: item for item in records["transactions"]}
    valid_diary = {item["id"]: item for item in records["diary"]}
    valid_todos = {item["id"]: item for item in records["todos"]}
    allowed_accounts = {f"expenses:{item}" for item in snapshot["categories"]}
    normalized = {"transactions": [], "diary": [], "todos": []}
    seen_ids = {"transactions": set(), "diary": set(), "todos": set()}
    for item in suggestions.get("transactions", []):
        if not isinstance(item, dict) or str(item.get("id") or "") not in valid_transactions:
            raise WebError("AI 返回了无效的账目编号。")
        account = str(item.get("account") or "").strip()
        if not account:
            continue
        if not account.startswith("expenses:"):
            account = f"expenses:{account}"
        if not ACCOUNT_RE.fullmatch(account) or account not in allowed_accounts:
            raise WebError("AI 返回了不存在的账目分类，请先建立分类后重试。")
        if item["id"] in seen_ids["transactions"]:
            raise WebError("AI 重复返回了账目建议。")
        seen_ids["transactions"].add(item["id"])
        normalized["transactions"].append({"id": str(item["id"]), "account": account})
    for item in suggestions.get("diary", []):
        if not isinstance(item, dict) or str(item.get("id") or "") not in valid_diary:
            raise WebError("AI 返回了无效的日记编号。")
        try:
            tags = normalize_tags(item.get("tags"))
        except DailyLogError as error:
            raise WebError("AI 返回的日记标签格式无效。") from error
        if item["id"] in seen_ids["diary"]:
            raise WebError("AI 重复返回了日记建议。")
        seen_ids["diary"].add(item["id"])
        if tags:
            normalized["diary"].append({"id": str(item["id"]), "tags": tags})
    for item in suggestions.get("todos", []):
        if not isinstance(item, dict) or str(item.get("id") or "") not in valid_todos:
            raise WebError("AI 返回了无效的待办编号。")
        try:
            tags = normalize_tags(item.get("tags"))
        except DailyLogError as error:
            raise WebError("AI 返回的待办标签格式无效。") from error
        if item["id"] in seen_ids["todos"]:
            raise WebError("AI 重复返回了待办建议。")
        seen_ids["todos"].add(item["id"])
        if tags:
            normalized["todos"].append({"id": str(item["id"]), "tags": tags})
    return normalized


def _organizer_batches(records: dict, size: int = ORGANIZER_BATCH_SIZE) -> list[dict]:
    entries = [
        ("transactions", item) for item in records["transactions"]
    ] + [
        ("diary", item) for item in records["diary"]
    ] + [
        ("todos", item) for item in records["todos"]
    ]
    batches = []
    for start in range(0, len(entries), size):
        batch = {"transactions": [], "diary": [], "todos": []}
        for kind, item in entries[start:start + size]:
            batch[kind].append(item)
        batches.append(batch)
    return batches


def start_organizer_review(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("整理请求格式无效。")
    scope = str(payload.get("scope") or "unorganized")
    month = str(payload.get("month") or "") or None
    snapshot = organizer_snapshot(scope, month)
    records = _selected_organizer_records(payload, snapshot)
    if not any(records.values()):
        raise WebError("请先选择需要整理的记录。")
    review_id = database().create_organizer_review(scope, month, _organizer_batches(records))
    return database().organizer_review(review_id)


def process_organizer_review(review_id: str) -> dict:
    data = database()
    batch = data.claim_organizer_batch(review_id)
    if batch is None:
        return data.organizer_review(review_id)
    review = data.organizer_review(review_id)
    try:
        snapshot = organizer_snapshot(review["scope"], review["month"])
        suggestions = suggest_organizer_with_ai(
            batch["records"],
            CONFIG.ai_credentials(),
            context={"accounts": [f"expenses:{item}" for item in snapshot["categories"]], "tags": snapshot["knownTags"]},
        )
        normalized = _normalize_organizer_suggestions(suggestions, batch["records"], snapshot)
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("AI 历史复核批次失败：%s", error)
        message = str(error) or "AI 复核批次失败，请稍后重试。"
        data.fail_organizer_batch(review_id, batch["number"], message)
        return data.organizer_review(review_id)
    data.complete_organizer_batch(review_id, batch["number"], normalized)
    return data.organizer_review(review_id)


def retry_organizer_review(review_id: str) -> dict:
    database().retry_organizer_review(review_id)
    return database().organizer_review(review_id)


def apply_organizer(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("整理修改格式无效。")
    transactions = payload.get("transactions", [])
    diary = payload.get("diary", [])
    todos = payload.get("todos", [])
    if not isinstance(transactions, list) or not isinstance(diary, list) or not isinstance(todos, list):
        raise WebError("整理修改格式无效。")
    scope = str(payload.get("scope") or "unorganized")
    month = str(payload.get("month") or "") or None
    review_id = str(payload.get("reviewId") or "").strip() or None
    if review_id:
        if not ORGANIZER_ID.fullmatch(review_id):
            raise WebError("复核批次编号无效。")
        if database().organizer_review(review_id)["status"] != "completed":
            raise WebError("AI 复核尚未完成，请等待所有批次完成或先重试失败批次。")
    snapshot = organizer_snapshot(scope, month)
    allowed = {
        "transaction": {item["id"] for item in snapshot["transactions"]},
        "diary": {item["id"] for item in snapshot["diary"]},
        "todo": {item["id"] for item in snapshot.get("todos", [])},
    }
    for item in transactions:
        if not isinstance(item, dict) or str(item.get("id") or "") not in allowed["transaction"]:
            raise WebError("有账目已经变化，请刷新整理页后重试。")
    for item in diary:
        if not isinstance(item, dict) or str(item.get("id") or "") not in allowed["diary"]:
            raise WebError("有日记已经变化，请刷新整理页后重试。")
    for item in todos:
        if not isinstance(item, dict) or str(item.get("id") or "") not in allowed["todo"]:
            raise WebError("有待办已经变化，请刷新整理页后重试。")
    if not transactions and not diary and not todos:
        raise WebError("没有需要应用的修改。")
    changed: dict = {}
    organizer_kwargs = {"allow_existing": scope != "unorganized"}
    if review_id:
        organizer_kwargs["review_id"] = review_id
    result = _local_change(lambda: changed.update(database().apply_organizer(
        transactions, diary, todos, **organizer_kwargs
    )))
    result.update(changed)
    result["message"] = f"已整理 {changed.get('transactions', 0)} 笔账目、{changed.get('diary', 0)} 篇日记和 {changed.get('todos', 0)} 项待办"
    return result


def bulk_edit(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise WebError("批量编辑格式无效。")
    transactions = payload.get("transactions", [])
    diary = payload.get("diary", [])
    todos = payload.get("todos", [])
    if not all(isinstance(items, list) for items in (transactions, diary, todos)):
        raise WebError("批量编辑格式无效。")
    if not transactions and not diary and not todos:
        raise WebError("请先选择需要编辑的记录。")
    for items, label in ((transactions, "账目"), (diary, "日记"), (todos, "待办")):
        for item in items:
            if not isinstance(item, dict) or not ORGANIZER_ID.fullmatch(str(item.get("id") or "")):
                raise WebError(f"批量编辑的{label}编号无效。")
    changed: dict = {}
    result = _local_change(lambda: changed.update(database().apply_organizer(
        transactions, diary, todos, allow_existing=True
    )))
    result.update(changed)
    result["message"] = f"已批量修改 {changed.get('transactions', 0)} 笔账目、{changed.get('diary', 0)} 篇日记和 {changed.get('todos', 0)} 项待办"
    return result


class DailyLogHandler(BaseHTTPRequestHandler):
    server_version = f"DailyLogLocal/{__version__}"

    def log_message(self, fmt: str, *args) -> None:
        LOGGER.info("HTTP %s", fmt % args)

    def send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise WebError("请求长度无效。") from error
        if length <= 0 or length > MAX_REQUEST_SIZE:
            raise WebError("请求内容为空或过大。")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WebError("请求不是有效的 JSON。") from error
        if not isinstance(value, dict):
            raise WebError("请求格式无效。")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/data":
            month = parse_qs(parsed.query).get("month", [None])[0]
            try:
                self.send_json(build_dashboard(month, database(), subscribed_events()))
            except Exception as error:  # noqa: BLE001
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/inbox":
            try:
                self.send_json({"items": database().list_inbox_items()})
            except Exception as error:  # noqa: BLE001
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        inbox_match = INBOX_ROUTE.fullmatch(parsed.path)
        if inbox_match and inbox_match.group(2) is None:
            try:
                self.send_json(database().get_inbox_item(inbox_match.group(1)))
            except Exception as error:  # noqa: BLE001
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/organize":
            try:
                query = parse_qs(parsed.query)
                scope = query.get("scope", ["unorganized"])[0]
                month = query.get("month", [None])[0]
                self.send_json(organizer_snapshot(scope, month))
            except Exception as error:  # noqa: BLE001
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        review_match = ORGANIZER_REVIEW_ROUTE.fullmatch(parsed.path)
        if review_match and review_match.group(2) is None:
            try:
                self.send_json(database().organizer_review(review_match.group(1)))
            except Exception as error:  # noqa: BLE001
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/organize/history":
            try:
                limit = parse_qs(parsed.query).get("limit", [30])[0]
                self.send_json({"changes": database().list_organizer_changes(int(limit))})
            except Exception as error:  # noqa: BLE001
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/health":
            self.send_json({"status": "ok", "maintenance": worker().status()})
            return
        if parsed.path == "/api/maintenance/status":
            self.send_json(worker().status())
            return
        if parsed.path == "/api/backup/status":
            try:
                self.send_json(get_backup_status())
            except Exception as error:  # noqa: BLE001
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/settings":
            self.send_json(settings_public())
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self.read_json()
            todo_action = TODO_ACTION_ROUTE.fullmatch(urlparse(self.path).path)
            if todo_action:
                self.send_json(change_todo(todo_action.group(1), todo_action.group(2)))
                return
            if self.path == "/api/record":
                self.send_json(apply_plan(payload.get("plan")))
                return
            if self.path == "/api/inbox":
                self.send_json(create_inbox(payload), HTTPStatus.CREATED)
                return
            inbox_match = INBOX_ROUTE.fullmatch(urlparse(self.path).path)
            if inbox_match and inbox_match.group(2) == "process":
                self.send_json(process_inbox_item(inbox_match.group(1)))
                return
            if inbox_match and inbox_match.group(2) == "apply":
                self.send_json(apply_inbox_item(inbox_match.group(1), payload))
                return
            if self.path == "/api/onboarding/complete":
                self.send_json(complete_onboarding(payload))
                return
            if self.path == "/api/settings/test-ai":
                self.send_json(test_ai(payload))
                return
            if self.path == "/api/settings/test-backup":
                self.send_json(test_backup(payload))
                return
            if self.path == "/api/settings/data-dir":
                self.send_json(relocate_data_directory(payload))
                return
            if self.path == "/api/ai/record":
                self.send_json(record_with_ai(payload.get("text", "")))
                return
            if self.path == "/api/organize/suggest":
                self.send_json(suggest_organizer(payload))
                return
            if self.path == "/api/organize/reviews":
                self.send_json(start_organizer_review(payload), HTTPStatus.CREATED)
                return
            review_match = ORGANIZER_REVIEW_ROUTE.fullmatch(urlparse(self.path).path)
            if review_match and review_match.group(2) == "next":
                self.send_json(process_organizer_review(review_match.group(1)))
                return
            if review_match and review_match.group(2) == "retry":
                self.send_json(retry_organizer_review(review_match.group(1)))
                return
            if self.path == "/api/organize/apply":
                self.send_json(apply_organizer(payload))
                return
            if self.path == "/api/bulk-edit":
                self.send_json(bulk_edit(payload))
                return
            if self.path == "/api/categories/rename":
                self.send_json({"error": "旧分类迁移接口已停用。"}, HTTPStatus.GONE)
                return
            if self.path == "/api/categories":
                self.send_json(create_category(payload.get("name")), HTTPStatus.CREATED)
                return
            if self.path == "/api/categories/delete":
                self.send_json(delete_category(payload.get("name"), payload.get("migrateTo")))
                return
            if self.path == "/api/export":
                self.send_json(export_data(payload.get("format")))
                return
            if self.path == "/api/backup":
                self.send_json(backup_now(payload.get("message")))
                return
            if self.path == "/api/backup/restore":
                self.send_json(restore_latest(payload.get("password", "")))
                return
            if self.path == "/api/calendar/subscriptions":
                self.send_json(create_calendar_subscription(payload.get("name"), payload.get("url")), HTTPStatus.CREATED)
                return
            if self.path == "/api/calendar/subscriptions/toggle":
                self.send_json(toggle_calendar_subscription(payload.get("id"), payload.get("enabled")))
                return
            if self.path == "/api/calendar/subscriptions/delete":
                self.send_json(delete_calendar_subscription(payload.get("id")))
                return
            if self.path == "/api/calendar/subscriptions/refresh":
                self.send_json(refresh_calendar_subscription(payload.get("id")))
                return
            self.send_json({"error": "接口不存在。"}, HTTPStatus.NOT_FOUND)
        except (WebError, DailyLogError, BackupError, SubscriptionError) as error:
            LOGGER.warning("请求未完成：%s", error)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("请求处理失败")
            self.send_json({"error": f"操作失败：{error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            if urlparse(self.path).path == "/api/settings":
                self.send_json(update_settings(self.read_json()))
                ensure_subscription_refresh()
                return
            route = ITEM_ROUTE.fullmatch(urlparse(self.path).path)
            if not route:
                self.send_json({"error": "接口不存在。"}, HTTPStatus.NOT_FOUND)
                return
            payload = self.read_json()
            self.send_json(update_item(route.group(1), route.group(2), payload.get("item")))
        except (WebError, DailyLogError) as error:
            LOGGER.warning("PUT 请求未完成：%s", error)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("PUT 请求处理失败")
            self.send_json({"error": f"操作失败：{error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            route = ITEM_ROUTE.fullmatch(urlparse(self.path).path)
            if not route:
                self.send_json({"error": "接口不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(delete_item(route.group(1), route.group(2)))
        except (WebError, DailyLogError) as error:
            LOGGER.warning("DELETE 请求未完成：%s", error)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("DELETE 请求处理失败")
            self.send_json({"error": f"操作失败：{error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_static(self, requested_path: str) -> None:
        relative = requested_path.lstrip("/") or "index.html"
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        content = target.read_bytes()
        content_type, _ = mimetypes.guess_type(target.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (content_type or "application/octet-stream") + ("; charset=utf-8" if target.suffix in {".html", ".css", ".js"} else ""))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 daily-log 本地网页")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--desktop", action="store_true", help="使用 pywebview 桌面窗口和系统托盘")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="用户数据目录；默认使用当前系统的本机应用数据目录",
    )
    parser.add_argument(
        "--migrate-from",
        type=Path,
        help="仅首次启动使用：从旧版 daily-log 仓库显式迁移个人数据",
    )
    args = parser.parse_args(argv)
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if args.smoke_test:
        if not WEB_ROOT.exists():
            raise SystemExit(f"网页目录不存在：{WEB_ROOT}")
        return 0
    if args.desktop:
        from daily_log.desktop_app import main as desktop_main

        return desktop_main([item for item in raw_args if item != "--desktop"])
    if not WEB_ROOT.exists():
        raise SystemExit(f"网页目录不存在：{WEB_ROOT}")
    paths = AppPaths(args.data_dir.expanduser().resolve()) if args.data_dir else AppPaths.default()
    migration_source = args.migrate_from.expanduser().resolve() if args.migrate_from else None
    configure_runtime(paths, migration_source, explicit_data_dir=bool(args.data_dir or os.environ.get("DAILY_LOG_STATE_DIR")))
    instance = SingleInstance(paths.instance_lock)
    try:
        instance.acquire()
    except InstanceAlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 1
    server = None
    try:
        try:
            server = ThreadingHTTPServer((args.host, args.port), DailyLogHandler)
        except OSError as error:
            raise SystemExit(f"无法启动本地服务（端口 {args.port} 可能已被占用）。") from error
        try:
            initialize_runtime()
        except ValueError as error:
            raise SystemExit(str(error)) from error
        actual_port = server.server_address[1]
        url = f"http://{args.host}:{actual_port}"
        print(f"Daily Log 已启动：{url}")
        print("按 Ctrl+C 停止。")
        if not args.no_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDaily Log 已停止。")
        return 0
    finally:
        if server is not None:
            server.server_close()
        if AUTO_BACKUP is not None:
            AUTO_BACKUP.stop()
        if WORKER is not None:
            WORKER.stop(flush=True)
        instance.release()
        close_logging()


if __name__ == "__main__":
    sys.exit(main())
