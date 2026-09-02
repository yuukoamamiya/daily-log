"""Cross-platform lock for one Daily Log process per user data directory."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable


class InstanceAlreadyRunning(RuntimeError):
    """The selected application profile is already owned by another process."""


class SingleInstance:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None
        self._reopen_path = self.path.with_name(f".{self.path.name}.reopen")
        self._reopen_ready_path = self.path.with_name(f".{self.path.name}.desktop")
        self._reopen_stop = threading.Event()
        self._reopen_thread: threading.Thread | None = None

    @property
    def reopen_available(self) -> bool:
        return self._reopen_ready_path.exists()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0, os.SEEK_END)
                if self._handle.tell() == 0:
                    self._handle.write(b"\0")
                    self._handle.flush()
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._reopen_path.unlink(missing_ok=True)
            self._reopen_ready_path.unlink(missing_ok=True)
        except (OSError, BlockingIOError) as error:
            self._close_handle()
            raise InstanceAlreadyRunning("这个数据目录已经有 Daily Log 在运行。") from error

    def mark_reopen_available(self) -> None:
        """Advertise that this process can handle a second-launch request."""
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.desktop.tmp")
        temporary.write_text("desktop\n", encoding="ascii")
        os.replace(temporary, self._reopen_ready_path)

    def request_reopen(self) -> None:
        """Ask the owner of this profile to show its existing window."""
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text("show\n", encoding="ascii")
        os.replace(temporary, self._reopen_path)

    def start_reopen_listener(self, callback: Callable[[], None], *, interval: float = 0.15) -> None:
        """Watch for a second launch asking this process to show its window."""
        if self._reopen_thread and self._reopen_thread.is_alive():
            return
        self._reopen_stop.clear()

        def listen() -> None:
            while not self._reopen_stop.wait(interval):
                if not self._reopen_path.exists():
                    continue
                try:
                    callback()
                except Exception:
                    # Keep the request for a later poll if the window is not ready yet.
                    continue
                try:
                    self._reopen_path.unlink()
                except FileNotFoundError:
                    pass

        self._reopen_thread = threading.Thread(
            target=listen, name="daily-log-reopen-listener", daemon=True
        )
        self._reopen_thread.start()

    def stop_reopen_listener(self, *, timeout: float = 2.0) -> None:
        self._reopen_stop.set()
        if self._reopen_thread:
            self._reopen_thread.join(timeout=timeout)
        self._reopen_thread = None
        self._reopen_path.unlink(missing_ok=True)
        self._reopen_ready_path.unlink(missing_ok=True)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._close_handle()

    def _close_handle(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(self, *_args) -> None:
        self.release()
