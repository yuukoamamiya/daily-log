"""Frozen desktop-client entry point."""
from __future__ import annotations

try:
    from .desktop_app import main
except ImportError:  # PyInstaller executes this file as __main__.
    from daily_log.desktop_app import main


if __name__ == "__main__":
    raise SystemExit(main())
