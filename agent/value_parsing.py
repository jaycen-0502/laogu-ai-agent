from __future__ import annotations

from typing import Any


def parse_bool(value: Any, *, default: bool | None = None) -> bool:
    """Parse persisted or remote boolean values without truthiness surprises."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None and default is not None:
        return default
    raise ValueError("boolean value must be true/false, 1/0, yes/no or on/off")
