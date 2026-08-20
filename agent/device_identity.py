"""Stable local device identity used to bind an Agent credential to one PC."""

from __future__ import annotations

import hashlib
import os
import platform
import uuid


def _windows_machine_guid() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip()
    except (OSError, ImportError):
        return ""


def current_device_id() -> str:
    parts = [
        _windows_machine_guid(),
        platform.node(),
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""),
        str(uuid.getnode()),
    ]
    material = "|".join(item.strip() for item in parts if item and item.strip())
    digest = hashlib.sha256(material.encode("utf-8", errors="ignore")).hexdigest()
    return f"win-{digest[:48]}"
