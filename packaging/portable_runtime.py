"""Point a frozen portable build at a data directory beside the executable."""
from __future__ import annotations

import os
import sys
from pathlib import Path


if not os.environ.get("DAILY_LOG_STATE_DIR"):
    os.environ["DAILY_LOG_STATE_DIR"] = str(Path(sys.executable).resolve().parent / "data")
