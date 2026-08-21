"""Conservative, auditable safety gates for automation requests.

This is deliberately a narrow deny-list at the boundary.  It does not inspect
or persist credentials, and it never attempts to make a difficult judgment
about a user's identity or intent.  Requests that need a higher-risk action
must be rejected and handled by an explicit, human-reviewed product flow.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    code: str = "ALLOWED"
    message: str = "Allowed"


# Credentials, access-control bypass, privacy intrusion, spam and
# impersonation are intentionally out of scope for unattended automation.
_HIGH_RISK = re.compile(
    r"(?:cookie|session\s*token|access\s*token|refresh\s*token|password\s*dump|credential\s*(?:export|dump|harvest)|"
    r"验证码绕过|绕过验证码|captcha\s*bypass|evad(?:e|ing)\s*(?:detection|rate)|bypass\s*(?:security|login)|"
    r"bulk\s*(?:dm|message|follow)|批量(?:私信|关注|发信)|spam|垃圾信息|impersonat(?:e|ion)|冒充|doxx|人肉)",
    re.IGNORECASE,
)


def evaluate_task(task_type: str, params: dict[str, Any] | None = None) -> SafetyDecision:
    """Evaluate a task before it is persisted or dispatched to a device."""

    normalized_type = str(task_type or "").strip()
    if not normalized_type:
        return SafetyDecision(False, "TASK_TYPE_REQUIRED", "Task type is required")
    try:
        encoded = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return SafetyDecision(False, "PARAMETERS_INVALID", "Task parameters must be JSON serializable")
    if _HIGH_RISK.search(f"{normalized_type} {encoded}"):
        return SafetyDecision(False, "HIGH_RISK_AUTOMATION", "This high-risk automation is not permitted")
    return SafetyDecision(True)

