"""Repository-wide file transaction with rollback on failure."""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


PATTERNS = (
    "data/journal/*.journal",
    "data/diary/journal.txt",
    "data/diary/*.md",
    "data/todo/*.txt",
    "data/calendar/*.ics",
)


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def data_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    for pattern in PATTERNS:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return files


@contextlib.contextmanager
def data_transaction(root: Path):
    root = Path(root)
    before = {path.relative_to(root): path.read_bytes() for path in data_files(root)}
    try:
        yield
    except Exception:
        current = {path.relative_to(root) for path in data_files(root)}
        for relative in current - before.keys():
            (root / relative).unlink(missing_ok=True)
        for relative, content in before.items():
            atomic_write(root / relative, content)
        raise
