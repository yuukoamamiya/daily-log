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
from daily_log.data_location import relocate_profile, redirect_path, write_redirect  # noqa: E402
from daily_log.config import LocalConfig  # noqa: E402
from daily_log.paths import AppPaths  # noqa: E402
from daily_log.runtime import bootstrap_runtime, migrate_legacy_runtime  # noqa: E402
from daily_log.ai import parse_with_ai, test_ai_connection  # noqa: E402
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
    return current


def test_backup(payload: object) -> dict:
    settings = _candidate_backup_settings(payload)
    if settings["backend"] == "local":
        PATHS.backups.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "message": "本机备份目录可以正常使用。"}
    if settings["backend"] == "webdav":
        return test_webdav_connection(settings["webdav"])
    if settings["backend"] == "s3":
        return test_s3_connection(settings["s3"])
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


def backup_now(message: object = None) -> dict:
    global LAST_BACKUP_ERROR, LAST_BACKUP_AT, LAST_BACKUP_TARGET, BACKUP_PENDING
    if not BACKUP_LOCK.acquire(blocking=False):
        raise WebError("备份仍在处理中，请稍候。")
    try:
        worker().flush()
        settings = CONFIG.backup_settings()
        secrets_text = json.dumps(CONFIG.secrets(), ensure_ascii=False, indent=2) if settings["include_secrets"] else None
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
        target = upload_archive(archive, settings)
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
