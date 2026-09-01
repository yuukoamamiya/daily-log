"""Application-owned paths that stay separate from the installed program."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .database import default_state_dir


@dataclass(frozen=True)
class AppPaths:
    """All writable paths used by the local application."""

    state_dir: Path

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(default_state_dir())

    @property
    def database(self) -> Path:
        return self.state_dir / "daily-log.db"

    @property
    def config(self) -> Path:
        return self.state_dir / "config.ini"

    @property
    def portable_root(self) -> Path:
        return self.state_dir / "portable"

    @property
    def backups(self) -> Path:
        return self.state_dir / "backups"

    @property
    def exports(self) -> Path:
        return self.state_dir / "exports"

    @property
    def logs(self) -> Path:
        return self.state_dir / "logs"

    @property
    def instance_lock(self) -> Path:
        return self.state_dir / ".instance.lock"

    @property
    def restore_safety(self) -> Path:
        return self.state_dir / "restore-safety"

    @property
    def migration_marker(self) -> Path:
        return self.state_dir / ".runtime-layout-v1.json"

    def ensure(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        self.exports.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.restore_safety.mkdir(parents=True, exist_ok=True)
