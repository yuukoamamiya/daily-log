"""Local application logging with credential redaction."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "daily_log"
LOG_FILENAME = "daily-log.log"
_LOG_HANDLER: RotatingFileHandler | None = None

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|secret[_ -]?key|access[_ -]?key|password)\s*[:=]\s*)[^\s,;]+"),
)


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[已隐藏]", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


def configure_logging(log_directory: Path) -> Path:
    """Configure one rotating log file and return its path."""
    global _LOG_HANDLER
    log_directory = Path(log_directory)
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / LOG_FILENAME
    root_logger = logging.getLogger(LOGGER_NAME)
    root_logger.setLevel(logging.INFO)
    root_logger.propagate = False
    if _LOG_HANDLER is not None:
        root_logger.removeHandler(_LOG_HANDLER)
        _LOG_HANDLER.close()
    _LOG_HANDLER = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _LOG_HANDLER.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _LOG_HANDLER.addFilter(RedactionFilter())
    root_logger.addHandler(_LOG_HANDLER)
    logging.getLogger(LOGGER_NAME).info("应用日志已初始化")
    return log_path


def close_logging() -> None:
    global _LOG_HANDLER
    if _LOG_HANDLER is None:
        return
    root_logger = logging.getLogger(LOGGER_NAME)
    root_logger.removeHandler(_LOG_HANDLER)
    _LOG_HANDLER.close()
    _LOG_HANDLER = None


def log_path(log_directory: Path) -> Path:
    return Path(log_directory) / LOG_FILENAME

