"""Standalone source entry point for the universal Daily Log client."""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    program_root = Path(__file__).resolve().parents[1]
    scripts = program_root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    import web_server

    return web_server.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
