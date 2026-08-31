"""Make the bundled Qt and Shiboken DLL directories visible on Windows."""

from __future__ import annotations

import os
import sys


if sys.platform == "win32":
    bundle_root = os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    # PyInstaller keeps the Qt extension modules, Qt DLLs, and Shiboken DLLs
    # in separate directories. All three locations must be visible before
    # importing PySide6.QtWidgets.
    dll_dirs = [
        bundle_root,
        os.path.join(bundle_root, "PySide6"),
        os.path.join(bundle_root, "shiboken6"),
    ]
    _dll_handles = []

    # Python 3.8+ uses an explicit DLL directory list for extension imports.
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is not None:
        for dll_dir in dll_dirs:
            if os.path.isdir(dll_dir):
                _dll_handles.append(add_dll_directory(dll_dir))

    # Keep compatibility with native plugins that still consult PATH.
    existing_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(dll_dirs + [existing_path])
