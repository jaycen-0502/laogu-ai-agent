"""Safe, opt-in updater and reloader for the read-only automation engine.

Remote Python is never trusted merely because it was downloaded.  Updates are
enabled only when an HTTPS URL and a locally configured, trusted SHA-256 pin
(``LAOGU_ENGINE_UPDATE_SHA256``) are present.  The file is compiled and written
atomically; any network or validation failure leaves the existing file intact.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import logging
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request

logger = logging.getLogger("laogu-ai-agent.updater")
MAX_ENGINE_BYTES = 2 * 1024 * 1024


def get_file_sha256(filepath: str | os.PathLike[str]) -> str:
    path = Path(filepath)
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_allowed_url(remote_url: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(remote_url)
    hosts = {
        item.strip().lower()
        for item in os.getenv("LAOGU_ENGINE_UPDATE_HOSTS", "api.jaycwl.org").split(",")
        if item.strip()
    }
    return parts.scheme == "https" and bool(parts.hostname) and parts.hostname.lower() in hosts


def _validate_code(code: bytes) -> None:
    if not code or len(code) > MAX_ENGINE_BYTES:
        raise ValueError("engine update size is invalid")
    text = code.decode("utf-8")
    compile(text, "x_automation_engine.py", "exec")
    tree = ast.parse(text)
    # The engine is a read-only validator.  Reject obvious process/file/network
    # side-effect imports even when a trusted pin was accidentally misconfigured.
    blocked = {"os", "subprocess", "socket", "shutil", "ctypes", "winreg"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".", 1)[0] for alias in node.names}
            if names & blocked:
                raise ValueError("engine update contains blocked imports")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in blocked:
            raise ValueError("engine update contains blocked imports")


def check_and_update_engine(remote_url: str, local_path: str) -> bool:
    """Download a pinned engine update and atomically replace ``local_path``.

    Returns ``True`` only when a new file was installed.  Missing configuration,
    non-HTTPS URLs, hash mismatch, timeout, and offline errors silently fall
    back to the local engine and return ``False``.
    """
    remote_url = str(remote_url or "").strip()
    target = Path(local_path)
    trusted = os.getenv("LAOGU_ENGINE_UPDATE_SHA256", "").strip().lower()
    if not remote_url or not _is_allowed_url(remote_url) or len(trusted) != 64:
        return False
    try:
        request = urllib.request.Request(
            remote_url,
            headers={"Accept": "text/x-python", "User-Agent": "Laogu-Agent-Updater/1.0"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            code = response.read(MAX_ENGINE_BYTES + 1)
        digest = hashlib.sha256(code).hexdigest()
        if digest != trusted:
            logger.warning("engine update hash mismatch; keeping local file")
            return False
        if digest == get_file_sha256(target):
            return False
        _validate_code(code)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix=f".{target.name}.", delete=False) as temp:
            temp.write(code)
            temp.flush()
            os.fsync(temp.fileno())
            temporary = Path(temp.name)
        os.replace(temporary, target)
        return True
    except (OSError, UnicodeError, SyntaxError, ValueError, urllib.error.URLError, TimeoutError) as exc:
        logger.info("engine update unavailable; using local file: %s", exc)
        try:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def get_automation_engine_class(*, remote_url: str = "", local_path: str = ""):
    """Optionally update, then import/reload and return ``XAutomationEngine``."""
    if remote_url and local_path:
        check_and_update_engine(remote_url, local_path)
    importlib.invalidate_caches()
    module = importlib.import_module("agent.x_automation_engine")
    module = importlib.reload(module)
    return getattr(module, "XAutomationEngine")
