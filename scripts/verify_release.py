"""Validate a release manifest without reading production secrets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_COMPONENTS = {"server", "web", "desktop", "agent", "browser"}


def verify(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest unreadable: {exc}"]
    if not isinstance(payload, dict) or not payload.get("version"):
        errors.append("version is required")
    if not REQUIRED_COMPONENTS.issubset(set(payload.get("components", []))):
        errors.append("all runtime components must be listed")
    policy = payload.get("upgrade_policy", {})
    for key in ("backup_required", "run_alembic_before_restart", "rollback_on_failed_health_check"):
        if policy.get(key) is not True:
            errors.append(f"upgrade policy {key} must be true")
    safety = payload.get("safety", {})
    for key in ("credentials_in_release_artifacts", "unattended_high_risk_automation", "cookie_or_token_export"):
        if safety.get(key) is not False:
            errors.append(f"safety policy {key} must be false")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    errors = verify(args.manifest)
    if errors:
        for error in errors:
            print(f"RELEASE_INVALID: {error}")
        return 1
    print("RELEASE_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

