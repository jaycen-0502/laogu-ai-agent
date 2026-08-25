from __future__ import annotations

import json
import base64
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .server_client import DpapiProtector, protect_agent_directory


ALWAYS_ALLOWED_CAPABILITIES = frozenset({"local.view", "local.browser.stop"})
OFFLINE_ACCESS_PREFIX = "LGOFF1."


def _decode_b64(value: str) -> bytes:
    raw = str(value).strip()
    raw += "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw.encode("ascii"))


def _verify_lease(token: str, public_key_value: str) -> dict[str, Any]:
    if not token.startswith(OFFLINE_ACCESS_PREFIX):
        raise ValueError("Invalid offline access token")
    parts = token[len(OFFLINE_ACCESS_PREFIX):].split(".")
    if len(parts) != 2:
        raise ValueError("Invalid offline access token")
    raw = _decode_b64(parts[0])
    Ed25519PublicKey.from_public_bytes(_decode_b64(public_key_value)).verify(_decode_b64(parts[1]), raw)
    lease = json.loads(raw.decode("utf-8"))
    if not isinstance(lease, dict) or lease.get("version") != 1:
        raise ValueError("Invalid offline access payload")
    return lease


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class OfflineAccessStore:
    """Persist the last server-issued capability snapshot with Windows DPAPI."""

    def __init__(self, path: Path, *, protector=None):
        self.path = path
        self.protector = protector or DpapiProtector()
        self._lock = threading.RLock()

    def accept(
        self,
        response: dict[str, Any],
        *,
        agent_id: str,
        device_id: str,
        workspace_id: str,
    ) -> bool:
        token = str(response.get("token") or "")
        public_key = str(response.get("public_key") or "")
        try:
            lease = _verify_lease(token, public_key)
        except Exception:
            return False
        if (
            str(lease.get("agent_id") or "") != str(agent_id or "")
            or str(lease.get("device_id") or "") != str(device_id or "")
            or str(lease.get("workspace_id") or "") != str(workspace_id or "")
        ):
            return False
        current = self._load_stored()
        trusted_key = str(current.get("public_key") or "")
        if trusted_key and trusted_key != public_key:
            return False
        self._save({"token": token, "public_key": public_key, "lease": lease})
        return True

    def _save(self, stored_lease: dict[str, Any]) -> None:
        payload = json.dumps(stored_lease, ensure_ascii=False, separators=(",", ":"))
        stored = {"lease_protected": self.protector.protect(payload)}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            protect_agent_directory(self.path.parent)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)

    def _load_stored(self) -> dict[str, Any]:
        with self._lock:
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
                raw = self.protector.unprotect(str(stored.get("lease_protected") or ""))
                stored_lease = json.loads(raw)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return {}
        return stored_lease if isinstance(stored_lease, dict) else {}

    def load(self) -> dict[str, Any]:
        stored = self._load_stored()
        try:
            return _verify_lease(str(stored.get("token") or ""), str(stored.get("public_key") or ""))
        except Exception:
            return {}

    def revoke(self) -> None:
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def status(
        self,
        *,
        agent_id: str,
        device_id: str,
        workspace_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        lease = self.load()
        checked = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires_at = _parse_time(str(lease.get("expires_at") or ""))
        matches = bool(
            lease
            and str(lease.get("agent_id") or "") == str(agent_id or "")
            and str(lease.get("device_id") or "") == str(device_id or "")
            and str(lease.get("workspace_id") or "") == str(workspace_id or "")
        )
        valid = bool(matches and expires_at and expires_at > checked)
        capabilities = {
            str(item).strip()
            for item in lease.get("capabilities", [])
            if isinstance(item, str) and str(item).strip()
        } if valid else set()
        capabilities.update(ALWAYS_ALLOWED_CAPABILITIES)
        return {
            "valid": valid,
            "capabilities": sorted(capabilities),
            "expires_at": expires_at.isoformat() if valid and expires_at else "",
            "issued_at": str(lease.get("issued_at") or "") if valid else "",
        }
