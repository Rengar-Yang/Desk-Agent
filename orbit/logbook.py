"""Conversation and tool log. Each file grows to 100MB, then rotates."""

import logging
import re
import threading
from logging.handlers import RotatingFileHandler

from orbit.config import CONFIG_DIR

_LIMIT = 100 * 1024 * 1024
_BACKUPS = 3
_FIELD_LIMIT = 8000
_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|(api[_-]?key|token|authorization)\s*[:=]\s*\S+)",
    re.I,
)
_lock = threading.Lock()
_logger = None


def _redact(text):
    return _SECRET.sub("[已隐藏]", text or "")


def _clip(value):
    text = value if isinstance(value, str) else str(value)
    text = _redact(text).replace("\r", " ").replace("\n", "\\n")
    if len(text) <= _FIELD_LIMIT:
        return text
    return text[:_FIELD_LIMIT] + "…（已截断）"


def _get():
    global _logger
    if _logger is not None:
        return _logger
    with _lock:
        if _logger is not None:
            return _logger
        folder = CONFIG_DIR / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            folder / "orbit.log",
            maxBytes=_LIMIT,
            backupCount=_BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger = logging.getLogger("orbit.logbook")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        _logger = logger
        return logger


def record(category, text="", **fields):
    """Append one chat or execution line. Secrets are redacted.

    The first argument is positional so a field named kind or text cannot collide with it.
    """
    try:
        parts = [str(category)]
        if text:
            parts.append(_clip(text))
        for key, value in fields.items():
            if value is None or value == "":
                continue
            parts.append(f"{key}={_clip(value)}")
        _get().info(" ".join(parts))
    except OSError:
        return


def log_path():
    return CONFIG_DIR / "logs" / "orbit.log"
