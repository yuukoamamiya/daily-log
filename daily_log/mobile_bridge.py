"""Internal phone-bridge contracts and the local retrying coordinator.

The bridge is deliberately narrower than module synchronization. A provider
can read Inbox items and publish one complete Dashboard snapshot; it never
writes the SQLite source of truth directly.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .dashboard import build_dashboard
from .database import DailyLogDatabase


SNAPSHOT_TYPE = "daily-log-dashboard"
SNAPSHOT_VERSION = 1


class MobileBridgeError(RuntimeError):
    """An expected provider or bridge operation failure."""


class MobileBridgeProvider(Protocol):
    """The minimum independent interface needed by the phone bridge."""

    provider_id: str

    def read_inbox(self) -> Iterable["BridgeInboxItem | Mapping[str, object]"]:
        """Return remote Inbox items without modifying local SQLite."""

    def publish_dashboard(self, snapshot: Mapping[str, object]) -> None:
        """Publish a complete read-only Dashboard snapshot."""


@dataclass(frozen=True)
class BridgeInboxItem:
    """A remote Inbox item identified independently of its text."""

    source_id: str
    text: str

    @classmethod
    def from_payload(cls, payload: object) -> "BridgeInboxItem":
        if isinstance(payload, cls):
            item = payload
        elif isinstance(payload, Mapping):
            source_id = payload.get("source_id", payload.get("sourceId", payload.get("id", "")))
            text = payload.get("text", payload.get("rawText", payload.get("raw_text", "")))
            item = cls(str(source_id or "").strip(), str(text or ""))
        else:
            raise MobileBridgeError("远程 Inbox 项目格式无效。")
        if not item.source_id or len(item.source_id) > 500:
            raise MobileBridgeError("远程 Inbox 项目缺少有效来源 ID。")
        if not item.text.strip() or len(item.text) > 20_000:
            raise MobileBridgeError("远程 Inbox 项目缺少有效文本。")
        return item


def build_dashboard_snapshot(
    database: DailyLogDatabase,
    month: str | None = None,
) -> dict:
    """Build the stable, complete four-module snapshot sent to a phone."""
    snapshot = build_dashboard(month, database)
    return {
        "snapshotType": SNAPSHOT_TYPE,
        "schemaVersion": SNAPSHOT_VERSION,
        **snapshot,
    }


def dashboard_snapshot_json(snapshot: Mapping[str, object]) -> str:
    """Serialize a snapshot deterministically for providers that send JSON."""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error_message(error: object) -> str:
    return str(error or "手机桥接操作失败。")[:2_000]


class MobileBridgeService:
    """Keep provider failures isolated from local data operations."""

    def __init__(
        self,
        database: DailyLogDatabase,
        provider: MobileBridgeProvider,
        *,
        dashboard_builder: Callable[[DailyLogDatabase, str | None], dict] = build_dashboard_snapshot,
    ):
        self.database = database
        self.provider = provider
        self.dashboard_builder = dashboard_builder

    @property
    def provider_id(self) -> str:
        identifier = str(getattr(self.provider, "provider_id", "")).strip()
        if not identifier:
            raise MobileBridgeError("手机桥接 Provider 缺少稳定 ID。")
        return identifier[:120]

    def publish_dashboard(self, month: str | None = None) -> dict:
        """Publish only after the local read model has been built successfully."""
        try:
            snapshot = self.dashboard_builder(self.database, month)
            if not isinstance(snapshot, dict):
                raise MobileBridgeError("Dashboard 快照格式无效。")
            self.provider.publish_dashboard(copy.deepcopy(snapshot))
            return {"ok": True, "snapshot": snapshot}
        except Exception as error:  # provider errors must be retriable, not fatal
            return {"ok": False, "snapshot": None, "error": _error_message(error)}

    def pull_inbox(
        self,
        *,
        process_inbox: Callable[[str], object] | None = None,
    ) -> dict:
        """Import remote Inbox items idempotently, optionally handing them to AI."""
        try:
            remote_items = list(self.provider.read_inbox())
        except Exception as error:  # offline phone input remains remote for a retry
            return {"ok": False, "imported": 0, "duplicates": 0, "processed": 0, "errors": [_error_message(error)]}

        imported = 0
        duplicates = 0
        processed = 0
        errors: list[str] = []
        for raw_item in remote_items:
            try:
                remote = BridgeInboxItem.from_payload(raw_item)
                existing = self.database.get_inbox_item_by_source(self.provider_id, remote.source_id)
                local = self.database.create_inbox_item(
                    remote.text,
                    source_provider=self.provider_id,
                    source_id=remote.source_id,
                )
                if existing is None:
                    imported += 1
                else:
                    duplicates += 1
                retryable = existing is None or existing["status"] in {"pending", "failed"}
                if process_inbox is not None and retryable:
                    process_inbox(local["id"])
                    processed += 1
            except Exception as error:
                errors.append(_error_message(error))
        return {
            "ok": not errors,
            "imported": imported,
            "duplicates": duplicates,
            "processed": processed,
            "errors": errors,
        }

    def sync_once(
        self,
        month: str | None = None,
        *,
        process_inbox: Callable[[str], object] | None = None,
    ) -> dict:
        """Run the two independent bridge capabilities in one best-effort pass."""
        published = self.publish_dashboard(month)
        pulled = self.pull_inbox(process_inbox=process_inbox)
        return {
            "ok": bool(published["ok"] and pulled["ok"]),
            "published": published,
            "pulled": pulled,
        }


class MockMobileBridgeProvider:
    """Offline provider for development, acceptance tests and future adapters."""

    provider_id = "mock"

    def __init__(
        self,
        inbox: Iterable[BridgeInboxItem | Mapping[str, object]] = (),
        *,
        read_failures: int = 0,
        publish_failures: int = 0,
    ):
        self._inbox = [BridgeInboxItem.from_payload(item) for item in inbox]
        self.read_failures_remaining = max(0, int(read_failures))
        self.publish_failures_remaining = max(0, int(publish_failures))
        self.published_dashboards: list[dict] = []

    def add_inbox_item(self, source_id: str, text: str) -> None:
        self._inbox.append(BridgeInboxItem(source_id, text))

    def read_inbox(self) -> list[BridgeInboxItem]:
        if self.read_failures_remaining:
            self.read_failures_remaining -= 1
            raise MobileBridgeError("Mock Provider 模拟离线。")
        return list(self._inbox)

    def fetch_inbox(self) -> list[BridgeInboxItem]:
        """Compatibility alias for adapters that call the operation fetch."""
        return self.read_inbox()

    def publish_dashboard(self, snapshot: Mapping[str, object]) -> None:
        if self.publish_failures_remaining:
            self.publish_failures_remaining -= 1
            raise MobileBridgeError("Mock Provider 模拟发布失败。")
        self.published_dashboards.append(copy.deepcopy(dict(snapshot)))


class MobileBridgeWorker:
    """Debounced background bridge with retry-on-next-wake semantics."""

    def __init__(
        self,
        service: MobileBridgeService,
        *,
        process_inbox: Callable[[str], object] | None = None,
        debounce_seconds: float = 1.5,
        retry_seconds: float = 30.0,
    ):
        self.service = service
        self.process_inbox = process_inbox
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.retry_seconds = max(0.01, float(retry_seconds))
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._busy = False
        self._last_error: str | None = None
        self._last_run: str | None = None
        self._last_report: dict | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="daily-log-mobile-bridge", daemon=True)
        self._thread.start()
        self.notify()

    def notify(self) -> None:
        self._wake.set()

    def flush(self) -> dict:
        return self._sync()

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def status(self) -> dict:
        with self._state_lock:
            return {
                "busy": self._busy,
                "lastError": self._last_error,
                "lastRun": self._last_run,
                "lastReport": copy.deepcopy(self._last_report),
            }

    def _sync(self) -> dict:
        with self._state_lock:
            self._busy = True
        try:
            report = self.service.sync_once(process_inbox=self.process_inbox)
            error = None
            if not report["ok"]:
                errors = report["published"].get("error") or report["pulled"].get("errors")
                error = "; ".join(errors) if isinstance(errors, list) else str(errors or "手机桥接失败。")
            with self._state_lock:
                self._last_report = report
                self._last_error = error
                self._last_run = datetime.now().astimezone().isoformat(timespec="seconds")
            return report
        finally:
            with self._state_lock:
                self._busy = False

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            if self._stop.is_set():
                return
            if not self._wake.is_set():
                continue
            self._wake.clear()
            deadline = time.monotonic() + self.debounce_seconds
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self._wake.wait(timeout=remaining):
                    self._wake.clear()
                    deadline = time.monotonic() + self.debounce_seconds
            if self._stop.is_set():
                return
            report = self._sync()
            if not report["ok"]:
                self._wake.wait(timeout=self.retry_seconds)
                if not self._stop.is_set():
                    self._wake.set()
