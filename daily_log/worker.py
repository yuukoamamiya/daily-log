"""Background projection worker with debounce and crash-safe SQLite outbox."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .database import DailyLogDatabase
from .projection import project_pending


class ProjectionWorker:
    def __init__(
        self,
        database: DailyLogDatabase,
        root: Path,
        *,
        prepare: Callable[[], None] | None = None,
        debounce_seconds: float = 1.5,
    ):
        self.database = database
        self.root = Path(root)
        self.prepare = prepare
        self.debounce_seconds = debounce_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._project_lock = threading.Lock()
        self._busy = False
        self._last_error: str | None = None
        self._last_run: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="daily-log-projector", daemon=True)
        self._thread.start()
        if self.database.maintenance_status()["pending"]:
            self.notify()

    def notify(self) -> None:
        self._wake.set()

    def flush(self) -> None:
        """Synchronously project all currently pending operations."""
        self._project()

    def stop(self, *, flush: bool = False, timeout: float = 10.0) -> None:
        if flush and self.database.maintenance_status()["pending"]:
            try:
                self._project()
            except Exception:
                pass
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def status(self) -> dict:
        database_status = self.database.maintenance_status()
        with self._state_lock:
            return {
                **database_status,
                "busy": self._busy,
                "lastError": self._last_error or database_status.get("lastError"),
                "lastRun": self._last_run,
            }

    def _set_state(self, *, busy: bool | None = None, error: str | None | object = ...) -> None:
        with self._state_lock:
            if busy is not None:
                self._busy = busy
            if error is not ...:
                self._last_error = error  # type: ignore[assignment]

    def _project(self) -> None:
        # A manual backup may flush while the debounce thread is waking up.
        # Serializing projection keeps both callers from applying one outbox row.
        with self._project_lock:
            self._set_state(busy=True)
            try:
                while self.database.maintenance_status()["pending"] and not self._stop.is_set():
                    project_pending(self.database, self.root, prepare=self.prepare)
                with self._state_lock:
                    self._last_error = None
                    self._last_run = datetime.now().astimezone().isoformat(timespec="seconds")
            except Exception as error:
                self._set_state(error=str(error))
                raise
            finally:
                self._set_state(busy=False)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            if self._stop.is_set():
                break
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
                break
            try:
                self._project()
            except Exception:
                # Keep jobs in the outbox. A new user action or app restart retries.
                continue
