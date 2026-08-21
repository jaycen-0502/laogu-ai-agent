"""Safe versioned updater and dynamic loader for the read-only Python engine."""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.request


logger = logging.getLogger("laogu-ai-agent.updater")
MAX_ENGINE_BYTES = 2 * 1024 * 1024
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EngineUpdateError(RuntimeError):
    pass


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
        raise EngineUpdateError("Engine update size is invalid")
    try:
        text = code.decode("utf-8")
        compile(text, "x_automation_engine.py", "exec")
        tree = ast.parse(text)
    except (UnicodeError, SyntaxError) as exc:
        raise EngineUpdateError("Engine update is not valid UTF-8 Python") from exc

    # The downloadable engine is deliberately read-only.  Block direct system,
    # filesystem, subprocess and network primitives even though transport is
    # already authenticated and pinned by SHA-256.
    blocked = {
        "builtins", "ctypes", "ftplib", "http", "importlib", "os", "pathlib",
        "requests", "shutil", "socket", "subprocess", "urllib", "winreg",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", 1)[0] for alias in node.names}
            if roots & blocked:
                raise EngineUpdateError("Engine update contains a blocked import")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".", 1)[0] in blocked:
                raise EngineUpdateError("Engine update contains a blocked import")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"breakpoint", "compile", "eval", "exec", "open", "__import__"}:
                raise EngineUpdateError("Engine update contains a blocked operation")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))


def _load_module(path: Path, digest: str):
    name = f"laogu_dynamic_x_engine_{digest[:16]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise EngineUpdateError("Unable to create the engine module loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        engine_class = getattr(module, "XAutomationEngine")
        if not isinstance(engine_class, type) or not callable(getattr(engine_class, "run", None)):
            raise EngineUpdateError("Engine does not expose a compatible XAutomationEngine")
        return module, engine_class
    except Exception:
        sys.modules.pop(name, None)
        raise


def install_engine_update(manifest: dict[str, Any], source: bytes, cache_dir: str | os.PathLike[str]) -> bool:
    """Validate, cache and activate an authenticated server engine bundle."""
    version = str(manifest.get("version") or "").strip()
    digest = str(manifest.get("sha256") or "").strip().lower()
    declared_size = manifest.get("size")
    if not _VERSION.fullmatch(version) or not _SHA256.fullmatch(digest):
        raise EngineUpdateError("Engine manifest is invalid")
    if manifest.get("read_only") is not True:
        raise EngineUpdateError("Engine manifest is not marked read-only")
    if declared_size is not None and int(declared_size) != len(source):
        raise EngineUpdateError("Engine source size does not match its manifest")
    if hashlib.sha256(source).hexdigest() != digest:
        raise EngineUpdateError("Engine SHA-256 verification failed")
    _validate_code(source)

    root = Path(cache_dir)
    engine_path = root / "versions" / f"{version}-{digest[:12]}" / "x_automation_engine.py"
    state_path = root / "active.json"
    state = read_engine_state(root)
    if state.get("active_sha256") == digest and engine_path.is_file():
        return False

    _atomic_write(engine_path, source)
    try:
        _load_module(engine_path, digest)
    except Exception as exc:
        engine_path.unlink(missing_ok=True)
        raise EngineUpdateError(f"Engine compatibility check failed: {exc}") from exc

    previous_path = str(state.get("active_path") or "")
    previous_sha256 = str(state.get("active_sha256") or "")
    _atomic_json(
        state_path,
        {
            "active_version": version,
            "active_sha256": digest,
            "active_path": str(engine_path.relative_to(root)),
            "previous_path": previous_path,
            "previous_sha256": previous_sha256,
        },
    )
    return True


def read_engine_state(cache_dir: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(cache_dir) / "active.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_cached_path(root: Path, relative: str) -> Path | None:
    if not relative:
        return None
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def get_cached_automation_engine_class(cache_dir: str | os.PathLike[str]):
    """Load the active engine, rolling state back if that engine is damaged."""
    root = Path(cache_dir)
    state = read_engine_state(root)
    candidates = (
        ("active", str(state.get("active_path") or ""), str(state.get("active_sha256") or "")),
        ("previous", str(state.get("previous_path") or ""), str(state.get("previous_sha256") or "")),
    )
    last_error: Exception | None = None
    for label, relative, expected in candidates:
        path = _safe_cached_path(root, relative)
        if path is None or not _SHA256.fullmatch(expected):
            continue
        try:
            if get_file_sha256(path) != expected:
                raise EngineUpdateError("Cached engine integrity check failed")
            _validate_code(path.read_bytes())
            _, engine_class = _load_module(path, expected)
        except Exception as exc:
            last_error = exc
            continue
        if label == "previous":
            _atomic_json(
                root / "active.json",
                {
                    "active_version": "rollback",
                    "active_sha256": expected,
                    "active_path": relative,
                    "previous_path": "",
                    "previous_sha256": "",
                },
            )
            logger.warning("Rolled back to the previous cached automation engine")
        return engine_class
    if last_error:
        logger.warning("Cached automation engine is unusable: %s", last_error)
    return None


def sync_engine_from_server(server_client, cache_dir: str | os.PathLike[str]) -> bool:
    """Fetch the authenticated manifest and source, then activate atomically."""
    manifest = server_client.fetch_engine_manifest()
    if not isinstance(manifest, dict):
        raise EngineUpdateError("Server returned an invalid engine manifest")
    digest = str(manifest.get("sha256") or "").lower()
    state = read_engine_state(cache_dir)
    if state.get("active_sha256") == digest:
        return False
    source = server_client.fetch_engine_source(str(manifest.get("source_url") or ""))
    return install_engine_update(manifest, source, cache_dir)


def check_and_update_engine(remote_url: str, local_path: str) -> bool:
    """Legacy opt-in pinned URL updater retained for source installations."""
    remote_url = str(remote_url or "").strip()
    target = Path(local_path)
    trusted = os.getenv("LAOGU_ENGINE_UPDATE_SHA256", "").strip().lower()
    if not remote_url or not _is_allowed_url(remote_url) or not _SHA256.fullmatch(trusted):
        return False
    try:
        request = urllib.request.Request(remote_url, headers={"Accept": "text/x-python", "User-Agent": "Laogu-Agent-Updater/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            source = response.read(MAX_ENGINE_BYTES + 1)
        if hashlib.sha256(source).hexdigest() != trusted or trusted == get_file_sha256(target):
            return False
        _validate_code(source)
        _atomic_write(target, source)
        return True
    except (OSError, EngineUpdateError, urllib.error.URLError, TimeoutError) as exc:
        logger.info("Engine update unavailable; using local file: %s", exc)
        return False


def get_automation_engine_class(*, remote_url: str = "", local_path: str = "", cache_dir: str = ""):
    """Return cached server engine when valid, otherwise the bundled engine."""
    if cache_dir:
        cached = get_cached_automation_engine_class(cache_dir)
        if cached is not None:
            return cached
    if remote_url and local_path:
        check_and_update_engine(remote_url, local_path)
    importlib.invalidate_caches()
    module = importlib.import_module("agent.x_automation_engine")
    module = importlib.reload(module)
    return getattr(module, "XAutomationEngine")
