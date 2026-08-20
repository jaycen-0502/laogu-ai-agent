from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import ipaddress
import re
import threading
from typing import Any

from fastapi import Request

from .models import AuditLog


SECRET_PATTERN = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._~+/-]+|lag_[A-Za-z0-9_-]{12,}|agent[_ -]?token|x[_ -]?token|jwt|password|cookie|session|authorization|api[_ -]?key|\btoken\b|\bsecret\b)")
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(pass(word)?|secret|token|authorization|cookie|session|api[_-]?key|access[_-]?key|private[_-]?key|bearer)"
)


def _sensitive_key(key: str) -> bool:
    normalized = key.strip().lower()
    # Capability metadata such as cookie_read_supported is deliberately safe to
    # expose; only value-bearing fields are redacted.
    if normalized.endswith(("_supported", "_allowed", "_status", "_capability", "evidence")):
        return False
    return bool(SENSITIVE_KEY_PATTERN.search(normalized))


def redact(value: Any) -> str:
    text = str(value or "")
    return "[REDACTED]" if SECRET_PATTERN.search(text) else text[:500]


def redact_payload(value: Any, *, max_depth: int = 8) -> Any:
    """Return a JSON-safe copy with secret-looking fields and strings removed.

    Command and task results originate from agents and browser integrations. They
    must be treated as untrusted input before being persisted or returned to an
    administrator. Values are never logged in their original form when their key
    identifies a credential; ordinary strings are still bounded and pattern
    redacted.
    """
    if max_depth <= 0:
        return "[REDACTED]"
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            key_text = str(key)
            output[key_text] = "[REDACTED]" if _sensitive_key(key_text) else redact_payload(item, max_depth=max_depth - 1)
        return output
    if isinstance(value, (list, tuple)):
        return [redact_payload(item, max_depth=max_depth - 1) for item in value[:500]]
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(value)


def client_ip(request: Request) -> str:
    direct = request.client.host if request.client else ""
    candidate = direct
    if direct in {"127.0.0.1", "::1"}:
        candidate = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or direct
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


class InMemoryRateLimiter:
    def __init__(self, window_seconds: int):
        self.window_seconds = max(1, window_seconds)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, now_timestamp: float) -> bool:
        with self._lock:
            events = self._events[key]
            threshold = now_timestamp - self.window_seconds
            while events and events[0] <= threshold:
                events.popleft()
            if len(events) >= max(1, limit):
                return False
            events.append(now_timestamp)
            return True


def audit(
    db,
    request: Request,
    *,
    action: str,
    result: str,
    user_id: str | None = None,
    workspace_id: str | None = None,
    agent_id: str | None = None,
    profile_id: str | None = None,
    task_id: str | None = None,
    script_id: str | None = None,
    script_version_id: str | None = None,
    resource_type: str = "",
    resource_id: str = "",
    message: str = "",
) -> AuditLog:
    item = AuditLog(
        user_id=user_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        profile_id=profile_id,
        task_id=task_id,
        script_id=script_id,
        script_version_id=script_version_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        ip=client_ip(request)[:80],
        user_agent=redact(request.headers.get("user-agent", ""))[:300],
        message=redact(message),
    )
    db.add(item)
    db.commit()
    return item


def audit_dict(item: AuditLog) -> dict[str, Any]:
    return {
        "audit_id": item.audit_id,
        "timestamp": item.timestamp.isoformat(),
        "user_id": item.user_id,
        "workspace_id": item.workspace_id,
        "agent_id": item.agent_id,
        "profile_id": item.profile_id,
        "task_id": item.task_id,
        "script_id": item.script_id,
        "script_version_id": item.script_version_id,
        "action": item.action,
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "result": item.result,
        "ip": item.ip,
        "user_agent": item.user_agent,
        "message": item.message,
    }
