from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import License, LicenseCheck, LicenseDevice, LicenseRevocation, User, now
from .schemas import LicenseCheckRequest, LicenseRegister, LicenseRenew, LicenseRevoke
from .security import audit, client_ip, redact


ACTIVATION_PREFIX = "LGACT1."


def _decode_b64(value: str) -> bytes:
    raw = value.strip()
    raw += "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw.encode("ascii"))


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _verify_activation(code: str, public_key_value: str) -> tuple[dict, str]:
    code = code.strip()
    if not code.startswith(ACTIVATION_PREFIX) or not public_key_value.strip():
        raise HTTPException(status_code=503 if not public_key_value.strip() else 422, detail="License verification unavailable" if not public_key_value.strip() else "Invalid activation code")
    parts = code[len(ACTIVATION_PREFIX):].split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=422, detail="Invalid activation code")
    try:
        payload_raw = _decode_b64(parts[0])
        signature = _decode_b64(parts[1])
        public_key = Ed25519PublicKey.from_public_bytes(_decode_b64(public_key_value))
        public_key.verify(signature, payload_raw)
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid activation code") from exc
    required = ("version", "licenseId", "deviceId", "installPublicKey", "requestNonce", "issuedAt", "expiresAt")
    if payload.get("version") != 1 or any(not str(payload.get(key) or "").strip() for key in required[1:]):
        raise HTTPException(status_code=422, detail="Invalid activation code")
    try:
        issued_at = _parse_time(payload["issuedAt"])
        expires_at = _parse_time(payload["expiresAt"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid activation code") from exc
    if not expires_at > issued_at:
        raise HTTPException(status_code=422, detail="Invalid activation code")
    payload["issued_at"] = issued_at
    payload["expires_at"] = expires_at
    payload["features"] = sorted({str(item).strip().lower() for item in (payload.get("features") or []) if str(item).strip()})
    return payload, hashlib.sha256(code.encode("utf-8")).hexdigest()


def _license_dict(item: License, device_count: int = 0, last_check: datetime | None = None) -> dict:
    return {
        "id": item.id,
        "license_id": item.license_id,
        "customer": item.customer,
        "issued_at": item.issued_at.isoformat(),
        "expires_at": item.expires_at.isoformat(),
        "features": item.features or [],
        "status": item.status,
        "offline_grace_days": item.offline_grace_days,
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
        "device_count": device_count,
        "last_check": last_check.isoformat() if last_check else None,
    }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _masked_device_id(value: str) -> str:
    value = value.strip()
    if len(value) <= 12:
        return value
    return f"{value[:8]}...{value[-6:]}"


def _masked_ip(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if ":" in value:
        groups = value.split(":")
        return ":".join(groups[:2]) + ":***"
    parts = value.split(".")
    if len(parts) == 4:
        return ".".join(parts[:2]) + ".*.*"
    return "***"


def register_remote_license_routes(app, *, get_db: Callable, current_user: Callable, deny: Callable) -> None:
    """Register the remote license control plane.

    Admin endpoints accept signed activation codes and persist only metadata or
    hashes.  The public check endpoint is used by the browser and never
    requires the management JWT.
    """

    @app.post("/api/license/register")
    def register_license(body: LicenseRegister, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            deny(request, db, action="LICENSE_REGISTER", user=user)
        settings = request.app.state.settings
        payload, code_hash = _verify_activation(body.activation_code, settings.license_issuer_public_key)
        existing = db.scalar(select(License).where(License.license_id == payload["licenseId"]))
        if existing:
            if existing.activation_code_hash == code_hash:
                return {"ok": True, "idempotent": True, "license": _license_dict(existing)}
            raise HTTPException(status_code=409, detail="License already registered; use renew")
        item = License(
            license_id=payload["licenseId"], customer=str(payload.get("customer") or "")[:200],
            issued_at=payload["issued_at"], expires_at=payload["expires_at"], features=payload["features"],
            activation_code_hash=code_hash, status="ACTIVE", offline_grace_days=body.offline_grace_days,
            created_by=user.id, created_at=now(), updated_at=now(),
        )
        db.add(item); db.commit(); db.refresh(item)
        audit(db, request, action="LICENSE_REGISTER", result="SUCCESS", user_id=user.id, resource_type="license", resource_id=item.license_id, message="Signed license metadata registered")
        return {"ok": True, "idempotent": False, "license": _license_dict(item)}

    @app.post("/api/license/renew")
    def renew_license(body: LicenseRenew, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            deny(request, db, action="LICENSE_RENEW", user=user)
        payload, code_hash = _verify_activation(body.activation_code, request.app.state.settings.license_issuer_public_key)
        item = db.scalar(select(License).where(License.license_id == payload["licenseId"]))
        if not item:
            raise HTTPException(status_code=404, detail="License not found")
        if db.scalar(select(LicenseRevocation).where(LicenseRevocation.license_id == item.id)):
            raise HTTPException(status_code=409, detail="Revoked license cannot be renewed")
        item.customer = str(payload.get("customer") or "")[:200]
        item.issued_at = payload["issued_at"]; item.expires_at = payload["expires_at"]
        item.features = payload["features"]; item.activation_code_hash = code_hash
        if body.offline_grace_days is not None: item.offline_grace_days = body.offline_grace_days
        item.status = "ACTIVE"; item.updated_at = now()
        db.commit(); db.refresh(item)
        audit(db, request, action="LICENSE_RENEW", result="SUCCESS", user_id=user.id, resource_type="license", resource_id=item.license_id, message="Signed license metadata renewed")
        return {"ok": True, "license": _license_dict(item)}

    @app.post("/api/license/revoke")
    def revoke_license(body: LicenseRevoke, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            deny(request, db, action="LICENSE_REVOKE", user=user)
        item = db.scalar(select(License).where(License.license_id == body.license_id.strip()))
        if not item:
            raise HTTPException(status_code=404, detail="License not found")
        revocation = db.scalar(select(LicenseRevocation).where(LicenseRevocation.license_id == item.id))
        if not revocation:
            revocation = LicenseRevocation(license_id=item.id, reason=redact(body.reason)[:300], revoked_by=user.id, revoked_at=now())
            db.add(revocation)
        item.status = "REVOKED"; item.revoked_at = revocation.revoked_at; item.updated_at = now()
        db.commit()
        audit(db, request, action="LICENSE_REVOKE", result="SUCCESS", user_id=user.id, resource_type="license", resource_id=item.license_id, message="License revoked")
        return {"ok": True, "license": _license_dict(item)}

    @app.get("/api/license/status")
    def license_status(license_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="Forbidden")
        query = select(License).order_by(License.created_at.desc())
        if license_id:
            query = query.where(License.license_id == license_id.strip())
        items = list(db.scalars(query))
        output = []
        for item in items:
            count = int(db.scalar(select(func.count()).select_from(LicenseDevice).where(LicenseDevice.license_id == item.id)) or 0)
            last = db.scalar(select(LicenseCheck.checked_at).where(LicenseCheck.license_id == item.id).order_by(LicenseCheck.checked_at.desc()).limit(1))
            output.append(_license_dict(item, count, last))
        if license_id:
            if not output:
                raise HTTPException(status_code=404, detail="License not found")
            return output[0]
        return output

    @app.get("/api/license/{license_id}/devices")
    def license_devices(license_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="Forbidden")
        item = db.scalar(select(License).where(License.license_id == license_id.strip()))
        if not item:
            raise HTTPException(status_code=404, detail="License not found")
        devices = list(db.scalars(select(LicenseDevice).where(LicenseDevice.license_id == item.id).order_by(LicenseDevice.last_seen_at.desc())))
        return [
            {
                "id": device.id,
                "device_id": _masked_device_id(device.device_id),
                "app_version": device.app_version,
                "first_seen_at": _aware(device.first_seen_at).isoformat(),
                "last_seen_at": _aware(device.last_seen_at).isoformat(),
                "last_ip": _masked_ip(device.last_ip),
                "status": device.status,
            }
            for device in devices
        ]

    @app.get("/api/license/{license_id}/checks")
    def license_checks(license_id: str, limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="Forbidden")
        item = db.scalar(select(License).where(License.license_id == license_id.strip()))
        if not item:
            raise HTTPException(status_code=404, detail="License not found")
        safe_limit = max(1, min(limit, 200))
        checks = list(db.scalars(select(LicenseCheck).where(LicenseCheck.license_id == item.id).order_by(LicenseCheck.checked_at.desc()).limit(safe_limit)))
        return [
            {
                "id": check.id,
                "device_id": _masked_device_id(check.device_id),
                "app_version": check.app_version,
                "result": check.result,
                "reason": check.reason,
                "checked_at": _aware(check.checked_at).isoformat(),
                "ip": _masked_ip(check.ip),
            }
            for check in checks
        ]

    @app.post("/api/license/check")
    def check_license(body: LicenseCheckRequest, request: Request, db: Session = Depends(get_db)):
        payload, code_hash = _verify_activation(body.activation_code, request.app.state.settings.license_issuer_public_key)
        external_id = str(payload["licenseId"])
        if payload["deviceId"] != body.device_id or payload["installPublicKey"] != body.install_public_key:
            raise HTTPException(status_code=403, detail="License device mismatch")
        item = db.scalar(select(License).where(License.license_id == external_id, License.activation_code_hash == code_hash))
        checked = now()
        if not item:
            db.add(LicenseCheck(external_license_id=external_id, device_id=body.device_id, app_version=body.app_version, result="NOT_REGISTERED", reason="not_registered", checked_at=checked, ip=client_ip(request)))
            db.commit()
            raise HTTPException(status_code=404, detail="License not registered")
        result = "VALID"; reason = "ok"
        device = db.scalar(select(LicenseDevice).where(LicenseDevice.license_id == item.id, LicenseDevice.device_id == body.device_id))
        key_hash = hashlib.sha256(body.install_public_key.encode("utf-8")).hexdigest()
        if item.status == "REVOKED" or db.scalar(select(LicenseRevocation).where(LicenseRevocation.license_id == item.id)):
            result, reason = "REVOKED", "revoked"
        elif _aware(item.expires_at) <= checked.astimezone(timezone.utc):
            result, reason = "EXPIRED", "expired"
        elif device and device.install_public_key_hash != key_hash:
            result, reason = "DEVICE_MISMATCH", "install_key_changed"
        elif not device:
            device = LicenseDevice(license_id=item.id, device_id=body.device_id, install_public_key_hash=key_hash, app_version=body.app_version, first_seen_at=checked, last_seen_at=checked, last_ip=client_ip(request), status="ACTIVE")
            db.add(device)
        if device:
            device.last_seen_at = checked; device.last_ip = client_ip(request); device.app_version = body.app_version
        db.add(LicenseCheck(license_id=item.id, external_license_id=external_id, device_id=body.device_id, app_version=body.app_version, result=result, reason=reason, checked_at=checked, ip=client_ip(request)))
        db.commit()
        valid = result == "VALID"
        return {"ok": valid, "state": result, "reason": reason, "license_id": item.license_id, "customer": item.customer, "issued_at": _aware(item.issued_at).isoformat(), "expires_at": _aware(item.expires_at).isoformat(), "features": item.features or [], "offline_grace_days": item.offline_grace_days, "server_time": checked.astimezone(timezone.utc).isoformat()}
