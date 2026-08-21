"""Release metadata shared by the server and the Windows runtime.

Keeping the version in one small, dependency-free module prevents the API,
Agent and desktop package from advertising different releases.  The
environment override is intentionally explicit so a signed build pipeline can
inject a CI build identifier without changing source files.
"""

from __future__ import annotations

import os
from typing import Any


DEFAULT_VERSION = "0.21.7"
RELEASE_CHANNEL = os.getenv("LAOGU_RELEASE_CHANNEL", "stable").strip().lower() or "stable"
VERSION = os.getenv("LAOGU_RELEASE_VERSION", DEFAULT_VERSION).strip() or DEFAULT_VERSION


def release_info(*, component: str) -> dict[str, Any]:
    """Return non-sensitive build metadata suitable for health/status APIs."""

    return {
        "version": VERSION,
        "channel": RELEASE_CHANNEL,
        "component": component,
        "architecture": {
            "server": "FastAPI + PostgreSQL",
            "desktop": "Windows desktop console + embedded Agent",
            "agent": "Windows background service loop",
            "browser": "Laogu Browser local API",
        },
    }
