"""First-run initialization and one-time migration from the legacy repository layout."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from .database import DailyLogDatabase
from .paths import AppPaths


PORTABLE_DIRECTORIES = (
    Path("data/journal"),
    Path("data/diary"),
    Path("data/todo"),
    Path("data/calendar"),
)


def initialize_portable_layout(root: Path) -> None:
    root = Path(root)
    for relative in PORTABLE_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    defaults = {
        Path("data/journal/ledger.journal"): "",
        Path("data/diary/journal.txt"): "",
        Path("data/todo/todo.txt"): "",
        Path("data/todo/done.txt"): "",
    }
    for relative, content in defaults.items():
        path = root / relative
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _legacy_has_data(root: Path) -> bool:
    root = Path(root)
    candidates = [
        *(root / "data/journal").glob("[0-9][0-9][0-9][0-9].journal"),
        root / "data/diary/journal.txt",
        root / "data/todo/todo.txt",
        root / "data/todo/done.txt",
        *(root / "data/calendar").glob("*.ics"),
    ]
    return any(path.is_file() and path.stat().st_size > 0 for path in candidates)


def _copy_legacy_projection(source_root: Path, portable_root: Path) -> None:
    for relative in PORTABLE_DIRECTORIES:
        source = source_root / relative
        target = portable_root / relative
        if not source.is_dir():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.tmp", "*.bak"),
        )


def _legacy_monthly_budget(root: Path) -> str | None:
    path = Path(root) / "data/journal/budget.journal"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s+\(expenses\)\s+([0-9]+(?:\.[0-9]+)?)\s*$", line)
        if match:
            return match.group(1)
    return None


def _initialize_runtime(
    database: DailyLogDatabase,
    paths: AppPaths,
    *,
    legacy_root: Path | None,
) -> dict:
    paths.ensure()
    if paths.migration_marker.exists():
        initialize_portable_layout(paths.portable_root)
        return {"mode": "existing", "portableRoot": str(paths.portable_root)}

    source = Path(legacy_root) if legacy_root is not None else None
    has_legacy = bool(source and _legacy_has_data(source))
    imported = False
    if not database.is_initialized():
        if has_legacy and source is not None:
            imported = database.import_text_data(source)
        else:
            database.initialize_empty()

    if has_legacy and source is not None:
        _copy_legacy_projection(source, paths.portable_root)
        legacy_budget = _legacy_monthly_budget(source)
        if legacy_budget is not None:
            database.set_monthly_budget(legacy_budget)
    initialize_portable_layout(paths.portable_root)

    details = {
        "format": 1,
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "legacy" if has_legacy else "empty",
        "importedDatabase": imported,
        "legacyRoot": str(source) if has_legacy and source is not None else None,
        "portableRoot": str(paths.portable_root),
    }
    temporary = paths.migration_marker.with_suffix(".tmp")
    temporary.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(paths.migration_marker)
    return details


def bootstrap_runtime(database: DailyLogDatabase, paths: AppPaths) -> dict:
    """Create or reopen an application profile without inspecting program data."""
    return _initialize_runtime(database, paths, legacy_root=None)


def migrate_legacy_runtime(
    database: DailyLogDatabase,
    paths: AppPaths,
    legacy_root: Path,
) -> dict:
    """Explicitly seed a new profile from the old repository layout exactly once."""
    legacy_root = Path(legacy_root).expanduser().resolve()
    if not legacy_root.is_dir():
        raise ValueError(f"旧版数据目录不存在：{legacy_root}")
    if paths.migration_marker.exists() or database.is_initialized():
        raise ValueError("当前用户数据目录已经初始化，不能再导入旧版数据。")
    if not _legacy_has_data(legacy_root):
        raise ValueError(f"目录中没有可迁移的旧版数据：{legacy_root}")
    return _initialize_runtime(database, paths, legacy_root=legacy_root)
