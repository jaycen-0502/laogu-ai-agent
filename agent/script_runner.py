from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
from typing import Any

from common.script_validation import (
    ScriptValidationError,
    source_sha256,
    validate_script_params,
    validate_script_source,
)

from .models import Task


class ScriptRunnerError(RuntimeError):
    pass


_SENSITIVE_KEY = re.compile(r"(?i)(password|cookie|session|authorization|jwt|token|secret|api.?key)")
_SENSITIVE_VALUE = re.compile(r"(?i)(bearer\s+\S+|lag_[A-Za-z0-9_-]{12,})")


class ScriptRunner:
    def __init__(self, hook_runner, working_dir: Path):
        self.hook_runner = hook_runner
        self.working_dir = working_dir

    def execute(self, task: Task) -> dict[str, Any]:
        bundle = task.metadata.get("script_bundle")
        if not isinstance(bundle, dict):
            raise ScriptRunnerError("Registered Script bundle is missing")
        expected_script = str(task.params.get("script_id") or "")
        expected_version = str(task.params.get("script_version_id") or "")
        if str(bundle.get("script_id") or "") != expected_script or str(bundle.get("script_version_id") or "") != expected_version:
            raise ScriptRunnerError("Script identity does not match Task")
        if str(bundle.get("language") or "").lower() != "javascript":
            raise ScriptRunnerError("Only JavaScript scripts are supported")
        source = bundle.get("source")
        expected_hash = str(bundle.get("sha256") or "")
        if not isinstance(source, str) or not expected_hash or source_sha256(source) != expected_hash:
            raise ScriptRunnerError("Script SHA256 integrity verification failed")
        try:
            validate_script_source(source)
            parameters = validate_script_params(task.params.get("params"), bundle.get("params_schema") or {})
        except ScriptValidationError as exc:
            raise ScriptRunnerError(str(exc)) from exc

        temporary_dir = self.working_dir / "logs" / "tmp" / "managed_scripts"
        temporary_dir.mkdir(parents=True, exist_ok=True)
        script_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".js",
                prefix=f"script-{task.task_id}-",
                dir=temporary_dir,
                delete=False,
            ) as script_file:
                script_file.write(source)
                script_path = Path(script_file.name)
            response = self.hook_runner.run_managed_script(
                profile_id=task.profile_id,
                task_id=task.task_id,
                params=parameters,
                timeout_seconds=task.timeout_seconds,
                script_path=script_path,
                cancel_check=task.metadata.get("cancel_check"),
            )
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)

        if response.get("ok") is False or response.get("status") in {"error", "failed"}:
            raise ScriptRunnerError(str(response.get("error") or response.get("message") or "Script execution failed"))
        sanitized = _sanitize(response)
        logs = _collect_logs(sanitized)
        payload = {
            "success": True,
            "script_id": expected_script,
            "script_version_id": expected_version,
            "result": sanitized.get("result", sanitized) if isinstance(sanitized, dict) else sanitized,
            "logs": logs,
        }
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
            raise ScriptRunnerError("Script result is too large")
        return payload


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        output = {}
        for key, item in list(value.items())[:500]:
            name = str(key)
            output[name] = "[REDACTED]" if _SENSITIVE_KEY.search(name) else _sanitize(item, depth + 1)
        return output
    if isinstance(value, list):
        return [_sanitize(item, depth + 1) for item in value[:500]]
    if isinstance(value, str):
        return "[REDACTED]" if _SENSITIVE_VALUE.search(value) else value[:10000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]


def _collect_logs(response: dict[str, Any]) -> list[Any]:
    candidates = response.get("logs", [])
    if not isinstance(candidates, list):
        nested = response.get("result")
        candidates = nested.get("logs", []) if isinstance(nested, dict) else []
    return candidates[:500] if isinstance(candidates, list) else []
