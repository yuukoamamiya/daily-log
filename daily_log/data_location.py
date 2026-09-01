"""Safe relocation of the user's application profile."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .errors import ValidationError


REDIRECT_NAME = ".profile-location.json"


def system_default_state_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return (base / "DailyLog").expanduser().resolve()


def redirect_path() -> Path:
    return system_default_state_dir() / REDIRECT_NAME


def read_redirect() -> Path | None:
    path = redirect_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        target = Path(str(payload["stateDir"])).expanduser()
        if payload.get("format") != 1 or not target.is_absolute():
            return None
        target = target.resolve()
        if target == path.parent:
            return None
        return target
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_redirect(target: Path) -> Path:
    target = Path(target).expanduser().resolve()
    destination = redirect_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"format": 1, "stateDir": str(target)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def validate_relocation_target(target: Path, current: Path, *, program_root: Path | None = None) -> Path:
    """Reject roots, protected locations, and recursive source/target copies."""
    target = Path(target).expanduser()
    if not target.is_absolute():
        raise ValidationError("数据目录必须填写绝对路径。")
    target = target.resolve()
    current = Path(current).expanduser().resolve()
    if target == current:
        raise ValidationError("新数据目录和当前目录相同，无需迁移。")
    if target.parent == target:
        raise ValidationError("不能把磁盘根目录作为 Daily Log 数据目录。")
    if target in current.parents or current in target.parents:
        raise ValidationError("新数据目录不能位于当前数据目录内，也不能包含当前数据目录。")

    protected = [Path.home().resolve() / "AppData/Local/Microsoft/Windows", Path(os.environ.get("SystemRoot", "C:/Windows")).resolve()]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(variable)
        if value:
            protected.append(Path(value).expanduser().resolve())
    if program_root is not None:
        protected.append(Path(program_root).expanduser().resolve())
    for root in protected:
        if target == root or root in target.parents:
            raise ValidationError("不能把系统目录或安装目录作为数据目录。")

    if target.exists():
        if target.is_symlink():
            raise ValidationError("目标数据目录不能是符号链接。")
        if not target.is_dir():
            raise ValidationError("目标路径不是文件夹。")
        if (target / "daily-log.db").exists():
            raise ValidationError("目标目录已经包含 daily-log.db；为避免覆盖，请先备份或选择空目录。")
        if any(target.iterdir()):
            raise ValidationError("目标目录必须为空，应用不会覆盖其中已有文件。")
    return target


def _copy_profile(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    ignored = {".instance.lock", REDIRECT_NAME}
    for base, directories, files in os.walk(source, followlinks=False):
        if any((Path(base) / name).is_symlink() for name in (*directories, *files)):
            raise ValidationError("当前数据目录包含符号链接，已停止迁移以避免越过目录边界。")
    for entry in source.iterdir():
        if entry.is_symlink():
            raise ValidationError("当前数据目录包含符号链接，已停止迁移以避免越过目录边界。")
        if entry.name in ignored or entry.name == "daily-log.db" or entry.name.endswith("-wal") or entry.name.endswith("-shm"):
            continue
        destination = target / entry.name
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, destination, dirs_exist_ok=True)
        elif entry.is_file():
            shutil.copy2(entry, destination)


def relocate_profile(source_database, current: Path, target: Path, *, program_root: Path | None = None) -> dict:
    """Copy a profile to an empty user-selected directory and validate its SQLite file."""
    target = validate_relocation_target(target, current, program_root=program_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        _copy_profile(Path(current), temporary)
        destination_db = temporary / "daily-log.db"
        destination = sqlite3.connect(destination_db)
        try:
            with source_database.session() as source:
                source.backup(destination)
            destination.commit()
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValidationError("新数据目录中的数据库校验失败，原数据未改变。")
        finally:
            destination.close()
        if target.exists():
            target.rmdir()
        temporary.replace(target)
    except ValidationError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error) as error:
        shutil.rmtree(temporary, ignore_errors=True)
        raise ValidationError("迁移数据目录失败，原数据未改变。") from error
    return {"path": str(target), "redirect": str(redirect_path())}
