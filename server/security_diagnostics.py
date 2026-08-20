from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from sqlalchemy import text

from .config import ROOT, ServerSettings
from .config_check import DEFAULT_SECRETS, ProductionConfigError, check_server_config


def _secret_status(value: str, *, minimum: int = 32) -> dict[str, Any]:
    encoded = bool(value)
    length_ok = len(value.encode()) >= minimum if value else False
    return {"configured": encoded, "length_ok": length_ok, "default": value in DEFAULT_SECRETS}


def _credential_key_status(value: str) -> dict[str, Any]:
    valid = False
    if value:
        try:
            valid = len(base64.urlsafe_b64decode(value.encode())) == 32
        except Exception:
            valid = False
    return {"configured": bool(value), "valid": valid}


def _path_status(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False, "world_writable": False}
    return {"exists": True, "world_writable": bool(stat.st_mode & 0o002), "mode": oct(stat.st_mode & 0o777)}


def configuration_diagnostics(settings: ServerSettings) -> dict[str, Any]:
    """Build a redacted production configuration report.

    This function deliberately returns only booleans, safe labels and file modes;
    it never returns a secret, connection string, token or key material.
    """
    try:
        check_server_config(settings)
        config_ok = True
        config_error = ""
    except ProductionConfigError as exc:
        config_ok = False
        config_error = str(exc)

    database_driver = ""
    database_ok = False
    try:
        from sqlalchemy.engine import make_url

        database = make_url(settings.database_url)
        database_driver = database.drivername
        database_ok = database_driver.startswith("postgresql") if settings.environment == "production" else True
    except Exception:
        config_error = config_error or "DATABASE_URL is invalid"

    root_status = _path_status(ROOT)
    image_path = Path(settings.ai_image_storage_path) if settings.ai_image_storage_path else ROOT / "data" / "ai-images"
    image_status = _path_status(image_path)
    checks = {
        "environment_production": settings.environment == "production",
        "debug_disabled": not settings.debug,
        "https_enabled": settings.https_enabled,
        "jwt_secret": _secret_status(settings.jwt_secret),
        "ai_credential_key": _credential_key_status(settings.ai_credential_key),
        "postgresql_configured": database_ok,
        "project_root": root_status,
        "image_storage": image_status,
    }
    failures = []
    if settings.environment == "production":
        failures.extend(name for name, value in checks.items() if value is False)
        jwt = checks["jwt_secret"]
        if not (jwt.get("configured") and jwt.get("length_ok") and not jwt.get("default")):
            failures.append("jwt_secret")
        credential = checks["ai_credential_key"]
        if not (credential.get("configured") and credential.get("valid")):
            failures.append("ai_credential_key")
        if not checks["project_root"].get("exists") or checks["project_root"].get("world_writable"):
            failures.append("project_root_permissions")
        if not checks["image_storage"].get("exists") or checks["image_storage"].get("world_writable"):
            failures.append("image_storage_permissions")
    return {
        "ok": config_ok and not failures,
        "environment": settings.environment,
        "config_valid": config_ok,
        "config_error": config_error,
        "database_driver": database_driver,
        "checks": checks,
        "failures": sorted(set(failures)),
    }


def database_diagnostic(db) -> dict[str, Any]:
    try:
        db.execute(text("SELECT 1"))
        return {"reachable": True}
    except Exception:
        return {"reachable": False}
