#!/usr/bin/env python3
"""Compatibility import for the local Daily Log web application."""
from __future__ import annotations

import json
import sys

from daily_log.dashboard import build_dashboard

__all__ = ["build_dashboard"]


if __name__ == "__main__":
    selected_month = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(build_dashboard(selected_month), ensure_ascii=False, indent=2))
