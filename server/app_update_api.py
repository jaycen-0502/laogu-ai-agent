"""Public desktop OTA checks and administrator release management."""

from __future__ import annotations

import hashlib
from email import policy
from email.parser import BytesParser
import os
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.release import VERSION

from .models import AppRelease, now
from .security import audit


_DEFAULT_PUBLISH_DIR = Path(__file__).resolve().parent.parent / "release" / "updates"
_PUBLISH_DIR = Path(
    os.getenv("LAOGU_APP_UPDATE_DIR", str(_DEFAULT_PUBLISH_DIR)).strip() or str(_DEFAULT_PUBLISH_DIR)
).expanduser()
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def _validate_version(value: str) -> str:
    version = str(value or "").strip()
    if not version or not _VERSION_RE.fullmatch(version):
        raise HTTPException(status_code=422, detail="版本号格式无效")
    return version


def _validate_channel(value: str) -> str:
    channel = str(value or "stable").strip().lower() or "stable"
    if not _CHANNEL_RE.fullmatch(channel):
        raise HTTPException(status_code=422, detail="更新渠道格式无效")
    return channel


def _version_key(value: str) -> tuple:
    """Return a deterministic semver-like key without a third-party package."""
    raw = str(value or "").strip().lstrip("vV")
    main, _, pre = raw.partition("-")
    number_parts = []
    for part in main.split("."):
        number_parts.append((0, int(part)) if part.isdigit() else (1, part.lower()))
    while len(number_parts) < 4:
        number_parts.append((0, 0))
    # Compare prerelease components through a homogeneous string key, avoiding
    # Python comparisons between integers and strings.
    pre_key = "" if not pre else ".".join(
        f"0:{int(part):020d}" if part.isdigit() else f"1:{part.lower()}" for part in pre.split(".")
    )
    return tuple(number_parts[:4]), (1, "") if not pre else (0, pre_key)


def _is_newer(candidate: str, current: str) -> bool:
    candidate_key = _version_key(candidate)
    current_key = _version_key(current)
    if candidate_key != current_key:
        return candidate_key > current_key
    # Preserve useful behavior for non-semver build identifiers.
    return str(candidate).strip() != str(current).strip() and str(candidate) > str(current)


def _release_payload(item: AppRelease | None, *, current_version: str = "", channel: str = "stable") -> dict[str, Any]:
    if item is None:
        return {
            "has_update": False,
            "latest_version": VERSION,
            "is_mandatory": False,
            "release_notes": "",
            "download_url": "",
            "sha256": "",
            "file_size": 0,
        }
    has_package = bool(item.package_path) and not item.package_deleted and Path(item.package_path).is_file()
    has_update = has_package and _is_newer(item.version, current_version)
    download_url = f"/api/v1/app/releases/{quote(item.version, safe='')}/package"
    if item.channel != "stable":
        download_url += f"?channel={quote(item.channel, safe='')}"
    return {
        "has_update": has_update,
        "latest_version": item.version,
        "is_mandatory": bool(item.is_mandatory and has_update),
        "release_notes": item.release_notes or "",
        "download_url": download_url if has_package else "",
        "sha256": item.sha256 if has_package else "",
        "file_size": int(item.file_size or 0) if has_package else 0,
    }


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


async def _read_upload(request: Request) -> tuple[dict[str, str], str, bytes]:
    """Read raw or multipart upload bodies without requiring python-multipart."""
    body = await request.body()
    settings = getattr(request.app.state, "settings", None)
    maximum = int(getattr(settings, "app_update_max_bytes", 0) or 0)
    if maximum > 0 and len(body) > maximum:
        raise HTTPException(status_code=413, detail="升级包超过服务器大小限制")
    content_type = request.headers.get("content-type", "").lower()
    fields: dict[str, str] = {}
    filename = request.headers.get("x-release-filename", "update.zip") or "update.zip"
    package = body
    if content_type.startswith("multipart/form-data"):
        header = "Content-Type: " + request.headers.get("content-type", "") + "\r\n\r\n"
        message = BytesParser(policy=policy.default).parsebytes(header.encode("ascii") + body)
        found_file = False
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            part_filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if part_filename is not None or name in {"file", "package", "upload"}:
                if not found_file or part_filename is not None:
                    package = payload
                    filename = os.path.basename(part_filename or filename)
                    found_file = True
                continue
            if name:
                fields[str(name)] = payload.decode("utf-8", errors="replace")
    return fields, filename, package


def _safe_filename(filename: str, version: str) -> str:
    candidate = Path(os.path.basename(str(filename or ""))).name
    if not candidate or candidate in {".", ".."}:
        candidate = f"laogu-update-{version}.zip"
    suffix = Path(candidate).suffix.lower()
    if suffix != ".zip":
        candidate = f"{Path(candidate).stem}.zip"
    return f"{version}-{candidate}"[:240]


def _write_package(version: str, filename: str, package: bytes) -> tuple[Path, str, int]:
    if not package:
        raise HTTPException(status_code=422, detail="升级包不能为空")
    _PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(filename, version)
    target = _PUBLISH_DIR / safe_name
    temporary = _PUBLISH_DIR / f".{safe_name}.{os.getpid()}.tmp"
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as output:
            for offset in range(0, len(package), 128 * 1024):
                chunk = package[offset : offset + 128 * 1024]
                output.write(chunk)
                digest.update(chunk)
        os.replace(temporary, target)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="无法保存升级包") from exc
    return target, digest.hexdigest(), len(package)


def register_app_update_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
) -> None:
    @app.get("/api/v1/app/check-update")
    def check_update(
        current_version: str = Query(default="", max_length=80),
        channel: str = Query(default="stable", max_length=32),
        db: Session = Depends(get_db),
    ):
        channel_name = _validate_channel(channel)
        releases = list(
            db.scalars(
                select(AppRelease)
                .where(AppRelease.channel == channel_name, AppRelease.is_active.is_(True))
                .order_by(AppRelease.created_at.desc())
            )
        )
        available = [
            item
            for item in releases
            if not item.package_deleted and item.package_path and Path(item.package_path).is_file()
        ]
        latest = max(available, key=lambda item: _version_key(item.version), default=None)
        return _release_payload(latest, current_version=current_version, channel=channel_name)

    @app.get("/api/v1/app/releases/{version}/package")
    def download_release_package(
        version: str,
        channel: str = Query(default="stable", max_length=32),
        db: Session = Depends(get_db),
    ):
        version = _validate_version(version)
        channel_name = _validate_channel(channel)
        item = db.scalar(
            select(AppRelease).where(
                AppRelease.version == version,
                AppRelease.channel == channel_name,
                AppRelease.is_active.is_(True),
                AppRelease.package_deleted.is_(False),
            )
        )
        package = Path(item.package_path) if item and item.package_path else None
        if not item or package is None or not package.is_file():
            raise HTTPException(status_code=404, detail="升级包不存在")
        filename = re.sub(r"[\r\n\"]", "_", os.path.basename(item.package_filename or package.name))
        return FileResponse(package, media_type="application/zip", filename=filename)

    @app.post("/api/v1/admin/releases/publish")
    @app.post("/api/admin/releases/publish")
    async def publish_release(
        request: Request,
        user=Depends(current_user),
        db: Session = Depends(get_db),
    ):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="仅系统管理员可以发布桌面版本")
        fields, filename, package = await _read_upload(request)
        version = _validate_version(
            fields.get("version")
            or request.headers.get("x-release-version")
            or request.query_params.get("version", "")
        )
        channel_name = _validate_channel(
            fields.get("channel")
            or request.headers.get("x-release-channel")
            or request.query_params.get("channel", "stable")
        )
        release_notes = (
            fields.get("release_notes")
            or fields.get("notes")
            or request.query_params.get("release_notes")
            or request.headers.get("x-release-notes")
            or ""
        ).strip()
        is_mandatory = _parse_bool(
            fields.get("is_mandatory")
            or fields.get("mandatory")
            or request.query_params.get("is_mandatory")
            or request.headers.get("x-release-mandatory")
        )
        target, digest, file_size = _write_package(version, filename, package)

        item = db.scalar(select(AppRelease).where(AppRelease.version == version, AppRelease.channel == channel_name))
        if item is None:
            item = AppRelease(version=version, channel=channel_name, created_by=user.id)
            db.add(item)
        item.release_notes = release_notes
        item.package_path = str(target)
        item.package_filename = os.path.basename(filename) or target.name
        item.sha256 = digest
        item.file_size = file_size
        item.is_mandatory = is_mandatory
        item.is_active = True
        item.package_deleted = False
        item.created_by = user.id
        item.created_at = now()
        db.commit()
        audit(
            db,
            request,
            action="APP_RELEASE_PUBLISHED",
            result="SUCCESS",
            user_id=user.id,
            resource_type="app_release",
            resource_id=item.id,
            message=f"version={version}; channel={channel_name}; bytes={file_size}",
        )
        download_url = f"/api/v1/app/releases/{quote(version, safe='')}/package"
        if channel_name != "stable":
            download_url += f"?channel={quote(channel_name, safe='')}"
        return {
            "ok": True,
            "version": version,
            "channel": channel_name,
            "release_notes": release_notes,
            "is_mandatory": is_mandatory,
            "sha256": digest,
            "file_size": file_size,
            "download_url": download_url,
        }

    @app.get("/api/v1/admin/releases")
    @app.get("/api/admin/releases")
    def list_releases(
        channel: str = Query(default="", max_length=32),
        user=Depends(current_user),
        db: Session = Depends(get_db),
    ):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="仅系统管理员可以查看版本列表")
        query = select(AppRelease).order_by(AppRelease.created_at.desc())
        if channel:
            channel_name = _validate_channel(channel)
            query = query.where(AppRelease.channel == channel_name)
        items = list(db.scalars(query))
        result = []
        for item in items:
            package = Path(item.package_path) if item.package_path else None
            file_exists = bool(package and package.is_file() and not item.package_deleted)
            result.append({
                "id": item.id,
                "version": item.version,
                "channel": item.channel,
                "release_notes": item.release_notes or "",
                "package_filename": item.package_filename or "",
                "file_size": item.file_size or 0,
                "sha256": item.sha256 or "",
                "is_mandatory": item.is_mandatory,
                "is_active": item.is_active,
                "package_deleted": item.package_deleted,
                "file_exists": file_exists,
                "download_url": f"/api/v1/app/releases/{quote(item.version, safe='')}/package" if file_exists else "",
                "created_at": item.created_at.isoformat() if item.created_at else None,
            })
        return {"items": result, "total": len(result)}

    @app.delete("/api/v1/admin/releases/{version}/package-file")
    @app.delete("/api/admin/releases/{version}/package-file")
    def delete_release_package(
        version: str,
        request: Request,
        channel: str = Query(default="stable", max_length=32),
        user=Depends(current_user),
        db: Session = Depends(get_db),
    ):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="仅系统管理员可以清理桌面安装包")
        version = _validate_version(version)
        channel_name = _validate_channel(channel)
        item = db.scalar(select(AppRelease).where(AppRelease.version == version, AppRelease.channel == channel_name))
        if item is None:
            raise HTTPException(status_code=404, detail="版本记录不存在")
        package = Path(item.package_path) if item.package_path else None
        released_bytes = 0
        if package and package.is_file():
            try:
                released_bytes = int(package.stat().st_size)
                os.unlink(package)
            except OSError as exc:
                raise HTTPException(status_code=500, detail="删除升级包失败") from exc
        item.package_deleted = True
        db.commit()
        released_mb = round(released_bytes / (1024 * 1024), 3)
        audit(
            db,
            request,
            action="APP_RELEASE_PACKAGE_DELETED",
            result="SUCCESS",
            user_id=user.id,
            resource_type="app_release",
            resource_id=item.id,
            message=f"version={version}; released_bytes={released_bytes}",
        )
        return {
            "ok": True,
            "version": version,
            "channel": channel_name,
            "released_bytes": released_bytes,
            "released_mb": released_mb,
            "released_size_mb": released_mb,
            "released_space_mb": released_mb,
        }

