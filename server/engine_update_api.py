"""Authenticated distribution endpoints for the reviewed read-only engine.

The server never accepts Python source from the web UI.  It only exposes the
engine that is already part of the deployed, reviewed server release.  Agents
authenticate with their existing device-bound Agent token before receiving a
manifest or source file.
"""

from __future__ import annotations

import hashlib
import ast
import json
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from common.release import VERSION

from .models import Agent


MAX_ENGINE_BYTES = 2 * 1024 * 1024
_PUBLISH_DIR = Path(__file__).resolve().parent.parent / "agent_data" / "engine_publish"


def _engine_path() -> Path:
    override = _PUBLISH_DIR / "x_automation_engine.py"
    return override if override.is_file() else Path(__file__).resolve().parent.parent / "agent" / "x_automation_engine.py"


def _published_version() -> str:
    try:
        value = json.loads((_PUBLISH_DIR / "manifest.json").read_text(encoding="utf-8"))
        return str(value.get("version") or VERSION)
    except (OSError, ValueError, TypeError):
        return VERSION


def _engine_bytes() -> tuple[bytes, str]:
    path = _engine_path()
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Automation engine is unavailable") from exc
    if not source or len(source) > MAX_ENGINE_BYTES:
        raise HTTPException(status_code=503, detail="Automation engine is unavailable")
    return source, hashlib.sha256(source).hexdigest()


def register_engine_update_routes(
    app: FastAPI,
    *,
    current_user: Callable,
    current_agent: Callable,
) -> None:
    @app.get("/api/agent/engine/manifest")
    def engine_manifest(agent: Agent = Depends(current_agent)):
        source, digest = _engine_bytes()
        return {
            "engine": "x_automation_engine",
            "version": _published_version(),
            "sha256": digest,
            "source_url": "/api/agent/engine/source",
            "size": len(source),
            "read_only": True,
        }

    @app.get("/api/agent/engine/source")
    def engine_source(agent: Agent = Depends(current_agent)):
        source, digest = _engine_bytes()
        return Response(
            content=source,
            media_type="text/x-python; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "X-Laogu-Engine-SHA256": digest,
                "X-Laogu-Engine-Version": _published_version(),
            },
        )

    @app.post("/api/admin/engine/publish")
    async def publish_engine(request: Request, user=Depends(current_user)):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="仅系统管理员可以发布自动化脚本")
        version = (request.headers.get("x-laogu-engine-version") or request.query_params.get("version") or "").strip()
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        if not version or len(version) > 64 or any(ch not in allowed for ch in version):
            raise HTTPException(status_code=422, detail="脚本版本号无效")
        source = await request.body()
        if not source or len(source) > MAX_ENGINE_BYTES:
            raise HTTPException(status_code=413, detail="脚本文件过大")
        try:
            ast.parse(source.decode("utf-8"))
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise HTTPException(status_code=422, detail="脚本不是有效的 UTF-8 Python 文件") from exc
        digest = hashlib.sha256(source).hexdigest()
        _PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        temporary = _PUBLISH_DIR / ".x_automation_engine.py.tmp"
        temporary.write_bytes(source)
        temporary.replace(_PUBLISH_DIR / "x_automation_engine.py")
        (_PUBLISH_DIR / "manifest.json").write_text(json.dumps({"version": version, "sha256": digest, "size": len(source)}), encoding="utf-8")
        return {"ok": True, "version": version, "sha256": digest, "size": len(source)}
