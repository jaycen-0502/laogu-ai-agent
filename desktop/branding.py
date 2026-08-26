from __future__ import annotations

import ctypes
from pathlib import Path
import sys

from PySide6.QtGui import QIcon


APP_USER_MODEL_ID = "Laogu.ControlCenter.Desktop"


def resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent


def brand_asset(filename: str) -> Path:
    return resource_root() / "desktop" / "assets" / filename


def application_icon() -> QIcon:
    for filename in ("laogu-control-center.ico", "laogu-control-center-256.png", "laogu-control-center-logo.svg"):
        path = brand_asset(filename)
        if path.is_file():
            return QIcon(str(path))
    return QIcon()


def configure_windows_app_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass
