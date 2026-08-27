"""Authenticated distribution and administration endpoints for Python engines.

Engines are read-only, versioned Python modules exposing ``XAutomationEngine``.
The default engine remains backward compatible with the original fixed endpoint.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from common.release import VERSION

from .models import Agent


MAX_ENGINE_BYTES = 2 * 1024 * 1024
_PUBLISH_DIR = Path(__file__).resolve().parent.parent / "agent_data" / "engine_publish"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BLOCKED_IMPORTS = {
    "builtins", "ctypes", "ftplib", "http", "importlib", "os", "pathlib",
    "requests", "shutil", "socket", "subprocess", "urllib", "winreg",
}


def _engine_id(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "default"
    if not _ID.fullmatch(value):
        raise HTTPException(status_code=422, detail="自动化引擎 ID 无效")
    return value


def _engine_dir(engine_id: str) -> Path:
    return _PUBLISH_DIR / _engine_id(engine_id)


def _source_path(engine_id: str) -> Path:
    target = _engine_dir(engine_id) / "x_automation_engine.py"
    if _engine_id(engine_id) == "default" and not target.is_file():
        legacy = _PUBLISH_DIR / "x_automation_engine.py"
        if legacy.is_file():
            return legacy
    return target


def _manifest_path(engine_id: str) -> Path:
    target = _engine_dir(engine_id) / "manifest.json"
    if _engine_id(engine_id) == "default" and not target.is_file():
        legacy = _PUBLISH_DIR / "manifest.json"
        if legacy.is_file():
            return legacy
    return target


def _security_findings(tree: ast.AST) -> list[str]:
    """Collect sensitive capabilities for audit metadata without blocking admins."""
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", 1)[0] for alias in node.names}
            findings.extend(sorted(roots & _BLOCKED_IMPORTS))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _BLOCKED_IMPORTS:
                findings.append(root)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"open", "eval", "exec", "compile", "__import__"}:
                findings.append(node.func.id)
    return sorted(set(findings))


def _validate_source(source: bytes) -> list[str]:
    """Validate compatibility while treating ADMIN uploads as trusted code."""
    if not source or len(source) > MAX_ENGINE_BYTES:
        raise HTTPException(status_code=413, detail="脚本文件超过 2 MiB 限制")
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise HTTPException(status_code=422, detail="脚本不是有效的 UTF-8 Python 文件") from exc
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "XAutomationEngine"]
    if not classes:
        raise HTTPException(status_code=422, detail="脚本必须提供 XAutomationEngine 类")
    if not any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "run"
        for item in classes[0].body
    ):
        raise HTTPException(status_code=422, detail="脚本必须提供 XAutomationEngine.run 方法")
    return _security_findings(tree)


def _read_manifest(engine_id: str) -> dict:
    path = _manifest_path(engine_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _engine_manifest(engine_id: str) -> dict:
    engine_id = _engine_id(engine_id)
    source_path = _source_path(engine_id)
    if not source_path.is_file() and engine_id == "default":
        source_path = Path(__file__).resolve().parent.parent / "agent" / "x_automation_engine.py"
    try:
        source = source_path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Automation engine is unavailable") from exc
    if not source or len(source) > MAX_ENGINE_BYTES:
        raise HTTPException(status_code=503, detail="Automation engine is unavailable")
    stored = _read_manifest(engine_id)
    return {
        "engine_id": engine_id,
        "name": str(stored.get("name") or ("默认自动化引擎" if engine_id == "default" else engine_id)),
        "description": str(stored.get("description") or ""),
        "engine": "x_automation_engine",
        "version": str(stored.get("version") or VERSION),
        "sha256": hashlib.sha256(source).hexdigest(),
        "source_url": "/api/agent/engine/source" if engine_id == "default" else f"/api/agent/engines/{engine_id}/source",
        "size": len(source),
        "read_only": True,
        "trusted_by_admin": stored.get("trusted_by_admin", False) is True,
        "security_warnings": list(stored.get("security_warnings") or []),
        "enabled": stored.get("enabled", True) is not False,
    }


def register_engine_update_routes(app: FastAPI, *, current_user: Callable, current_agent: Callable) -> None:
    @app.get("/api/admin/engines")
    def admin_engine_list(user=Depends(current_user)):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="仅系统管理员可以查看自动化脚本")
        items = [_engine_manifest("default")]
        if _PUBLISH_DIR.is_dir():
            for path in sorted(_PUBLISH_DIR.iterdir()):
                if path.is_dir() and path.name != "default" and _ID.fullmatch(path.name):
                    try:
                        items.append(_engine_manifest(path.name))
                    except HTTPException:
                        continue
        return {"items": items}

    @app.get("/api/agent/engines")
    def engine_list(agent: Agent = Depends(current_agent)):
        del agent
        items = [_engine_manifest("default")]
        if _PUBLISH_DIR.is_dir():
            for path in sorted(_PUBLISH_DIR.iterdir()):
                if path.is_dir() and path.name != "default" and _ID.fullmatch(path.name):
                    try:
                        item = _engine_manifest(path.name)
                    except HTTPException:
                        continue
                    if item.get("enabled") is not False:
                        items.append(item)
        return {"items": items}

    @app.get("/api/agent/engines/{engine_id}/manifest")
    def engine_manifest_named(engine_id: str, agent: Agent = Depends(current_agent)):
        del agent
        item = _engine_manifest(engine_id)
        if item.get("enabled") is False:
            raise HTTPException(status_code=404, detail="Automation engine is disabled")
        return item

    @app.get("/api/agent/engines/{engine_id}/source")
    def engine_source_named(engine_id: str, agent: Agent = Depends(current_agent)):
        del agent
        item = _engine_manifest(engine_id)
        if item.get("enabled") is False:
            raise HTTPException(status_code=404, detail="Automation engine is disabled")
        path = _source_path(engine_id)
        if engine_id == "default" and not path.is_file():
            path = Path(__file__).resolve().parent.parent / "agent" / "x_automation_engine.py"
        source = path.read_bytes()
        return Response(content=source, media_type="text/x-python; charset=utf-8", headers={
            "Cache-Control": "no-store",
            "X-Laogu-Engine-SHA256": item["sha256"],
            "X-Laogu-Engine-Version": item["version"],
        })

    # Backward-compatible default-engine endpoints.
    @app.get("/api/agent/engine/manifest")
    def engine_manifest(agent: Agent = Depends(current_agent)):
        return engine_manifest_named("default", agent)

    @app.get("/api/agent/engine/source")
    def engine_source(agent: Agent = Depends(current_agent)):
        return engine_source_named("default", agent)

    @app.post("/api/admin/engine/publish")
    async def publish_engine(request: Request, user=Depends(current_user)):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="仅系统管理员可以发布自动化脚本")
        engine_id = _engine_id(request.headers.get("x-laogu-engine-id") or request.query_params.get("engine_id") or "default")
        version = (request.headers.get("x-laogu-engine-version") or request.query_params.get("version") or "").strip()
        name = (request.headers.get("x-laogu-engine-name") or request.query_params.get("name") or engine_id).strip()[:120]
        description = (request.headers.get("x-laogu-engine-description") or request.query_params.get("description") or "").strip()[:500]
        if not _VERSION.fullmatch(version):
            raise HTTPException(status_code=422, detail="脚本版本号无效")
        source = await request.body()
        security_warnings = _validate_source(source)
        digest = hashlib.sha256(source).hexdigest()
        target = _engine_dir(engine_id)
        target.mkdir(parents=True, exist_ok=True)
        temporary = target / ".x_automation_engine.py.tmp"
        temporary.write_bytes(source)
        temporary.replace(_source_path(engine_id))
        _manifest_path(engine_id).write_text(json.dumps({
            "engine_id": engine_id, "name": name, "description": description,
            "version": version, "sha256": digest, "size": len(source), "enabled": True,
            "read_only": True, "trusted_by_admin": True, "security_warnings": security_warnings,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "engine_id": engine_id, "name": name, "version": version, "sha256": digest, "size": len(source), "trusted_by_admin": True, "security_warnings": security_warnings}

    @app.post("/api/admin/engine/{engine_id}/toggle")
    async def toggle_engine(engine_id: str, request: Request, user=Depends(current_user)):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="仅系统管理员可以管理自动化脚本")
        engine_id = _engine_id(engine_id)
        if engine_id == "default":
            raise HTTPException(status_code=422, detail="默认引擎不能禁用")
        manifest = _read_manifest(engine_id)
        if not manifest:
            raise HTTPException(status_code=404, detail="Automation engine not found")
        enabled = request.query_params.get("enabled", "true").lower() in {"1", "true", "yes"}
        manifest["enabled"] = enabled
        _manifest_path(engine_id).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "engine_id": engine_id, "enabled": enabled}
