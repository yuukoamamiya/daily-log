"""Run a best-effort action after the user has stopped making changes."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable


class IdleWorker:
    def __init__(
        self,
        action: Callable[[], None],
        settings: Callable[[], tuple[bool, int]],
    ):
        self.action = action
        self.settings = settings
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._generation = 0
        self._busy = False
        self._last_error: str | None = None
        self._last_run: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="daily-log-idle-backup", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        with self._lock:
            self._generation += 1
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def status(self) -> dict:
        enabled, idle_seconds = self.settings()
        with self._lock:
            return {
                "enabled": enabled,
                "idleSeconds": idle_seconds,
                "busy": self._busy,
                "lastError": self._last_error,
                "lastRun": self._last_run,
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                break
            enabled, idle_seconds = self.settings()
            if not enabled:
                continue
            with self._lock:
                generation = self._generation
            deadline = time.monotonic() + idle_seconds
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self._wake.wait(timeout=remaining):
                    self._wake.clear()
                    enabled, idle_seconds = self.settings()
                    if not enabled:
                        break
                    with self._lock:
                        generation = self._generation
                    deadline = time.monotonic() + idle_seconds
            if self._stop.is_set() or not enabled:
                continue
            with self._lock:
                if generation != self._generation:
                    continue
                self._busy = True
            try:
                self.action()
                with self._lock:
                    self._last_error = None
                    self._last_run = datetime.now().astimezone().isoformat(timespec="seconds")
            except Exception as error:  # Retried after the next local change or by manual backup.
                with self._lock:
                    self._last_error = str(error)
            finally:
                with self._lock:
                    self._busy = False
