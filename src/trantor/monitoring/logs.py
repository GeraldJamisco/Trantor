"""Structured JSON-lines logging with secret redaction."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

from ..safety import SecretRedactingFilter

__all__ = ["JsonFormatter", "configure_logging"]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "event", None)
        if isinstance(extra, dict):
            payload["event"] = extra
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(log_dir: str | Path, level: str = "INFO", secrets: list[str] | None = None) -> Path:
    """Write rotating JSON logs to ``log_dir/trantor.jsonl`` and plain text to stderr."""

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "trantor.jsonl"
    root = logging.getLogger("trantor")
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    redactor = SecretRedactingFilter(secrets)
    file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=10_000_000, backupCount=10, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(redactor)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    console.addFilter(redactor)
    root.addHandler(file_handler)
    root.addHandler(console)
    root.propagate = False
    return path
