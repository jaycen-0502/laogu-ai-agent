"""Authenticated distribution endpoints for the reviewed read-only engine.

The server never accepts Python source from the web UI.  It only exposes the
engine that is already part of the deployed, reviewed server release.  Agents
authenticate with their existing device-bound Agent token before receiving a
manifest or source file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Response

from common.release import VERSION

from .models import Agent


MAX_ENGINE_BYTES = 2 * 1024 * 1024


def _engine_path() -> Path:
    return Path(__file__).resolve().parent.parent / "agent" / "x_automation_engine.py"


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
    current_agent: Callable,
) -> None:
    @app.get("/api/agent/engine/manifest")
    def engine_manifest(agent: Agent = Depends(current_agent)):
        source, digest = _engine_bytes()
        return {
            "engine": "x_automation_engine",
            "version": VERSION,
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
                "X-Laogu-Engine-Version": VERSION,
            },
        )
