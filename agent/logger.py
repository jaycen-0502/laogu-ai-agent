import logging
from pathlib import Path
import re
import sys
import threading
from typing import Any


_LOGGER_LOCK = threading.Lock()
_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._~+/-]+|lag_[A-Za-z0-9_-]{12,}|agent[_ -]?token|x[_ -]?token|jwt|password|cookie|session|authorization|api[_ -]?key|\btoken\b|\bsecret\b)")


def _safe(value: Any) -> str:
    text = str(value)
    return "[REDACTED]" if _SECRET_PATTERN.search(text) else text


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _safe(super().format(record))


def build_logger(log_file: Path) -> logging.Logger:
    with _LOGGER_LOCK:
        resolved = log_file.resolve()
        logger = logging.getLogger(f"laogu-ai-agent.{abs(hash(str(resolved)))}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if logger.handlers:
            return logger

        log_file.parent.mkdir(parents=True, exist_ok=True)
        formatter = SafeFormatter(
            "[%(asctime)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # PyInstaller's windowed bootloader intentionally starts with
        # sys.stderr=None.  A StreamHandler created in that state keeps a
        # None stream forever and later raises "NoneType has no attribute
        # write" from worker threads.  Keep the durable file handler in all
        # modes and add console output only when a writable stream exists.
        stream = getattr(sys, "stderr", None)
        if stream is not None and callable(getattr(stream, "write", None)):
            console = logging.StreamHandler(stream)
            console.setFormatter(formatter)
            logger.addHandler(console)
        return logger


def log_task_event(
    logger: logging.Logger,
    *,
    task_id: str,
    profile_id: str,
    profile_name: str,
    status: str,
    operation: str,
    **fields: Any,
) -> None:
    parts = [
        f"task={task_id}",
        f"profile={profile_name}",
        f"profile_id={profile_id}",
        f"status={status}",
        f"operation={operation}",
    ]
    parts.extend(f"{key}={_safe(value)}" for key, value in fields.items() if value not in (None, ""))
    logger.info(" ".join(parts))
