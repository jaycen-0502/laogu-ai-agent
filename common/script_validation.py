from __future__ import annotations

import hashlib
import json
import re
from typing import Any


MAX_SCRIPT_BYTES = 512 * 1024
MAX_PARAMS_BYTES = 64 * 1024
MAX_SCHEMA_BYTES = 64 * 1024

_FORBIDDEN_SOURCE_PATTERNS = (
    (re.compile(r"\brequire\s*\("), "CommonJS require is not allowed"),
    (re.compile(r"\bmodule\s*(?:\.\s*require|\[\s*['\"]require['\"]\s*\])"), "module require is not allowed"),
    (re.compile(r"\bimport\s*\("), "dynamic import is not allowed"),
    (re.compile(r"\b(?:process|global|globalThis|Deno|Bun|__dirname|__filename)\b"), "system runtime access is not allowed"),
    (re.compile(r"\[\s*['\"](?:process|global|constructor|require)['\"]\s*\]"), "indirect runtime access is not allowed"),
    (re.compile(r"\.\s*constructor\b"), "constructor reflection is not allowed"),
    (re.compile(r"\b(?:child_process|worker_threads)\b"), "process execution is not allowed"),
    (re.compile(r"\b(?:eval|Function)\s*\("), "dynamic code evaluation is not allowed"),
    (re.compile(r"\bWebAssembly\b"), "WebAssembly is not allowed"),
)


class ScriptValidationError(ValueError):
    pass


def source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def validate_script_source(source: str) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ScriptValidationError("Script source is required")
    if len(source.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ScriptValidationError("Script source is too large")
    if not re.search(r"module\.exports\.run\s*=\s*async", source):
        raise ScriptValidationError("Script must export module.exports.run as an async function")
    for pattern, message in _FORBIDDEN_SOURCE_PATTERNS:
        if pattern.search(source):
            raise ScriptValidationError(message)
    return source


def validate_params_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(schema or {"type": "object", "properties": {}, "additionalProperties": False})
    if len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise ScriptValidationError("Parameter schema is too large")
    if normalized.get("type", "object") != "object":
        raise ScriptValidationError("Top-level parameter schema type must be object")
    properties = normalized.get("properties", {})
    required = normalized.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ScriptValidationError("Invalid parameter schema")
    if not set(required).issubset(properties):
        raise ScriptValidationError("Required parameter is missing from properties")
    for name, rule in properties.items():
        if not isinstance(name, str) or not isinstance(rule, dict):
            raise ScriptValidationError("Invalid parameter property")
        if rule.get("type") not in {"string", "integer", "number", "boolean", "array", "object", None}:
            raise ScriptValidationError(f"Unsupported parameter type: {rule.get('type')}")
    normalized.setdefault("additionalProperties", False)
    return normalized


def validate_script_params(params: dict[str, Any] | None, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params or {}, dict):
        raise ScriptValidationError("Script parameters must be an object")
    normalized = dict(params or {})
    if len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")) > MAX_PARAMS_BYTES:
        raise ScriptValidationError("Script parameters are too large")
    checked_schema = validate_params_schema(schema)
    properties = checked_schema.get("properties", {})
    missing = [name for name in checked_schema.get("required", []) if name not in normalized]
    if missing:
        raise ScriptValidationError(f"Missing required parameters: {', '.join(missing)}")
    if checked_schema.get("additionalProperties") is False:
        unexpected = sorted(set(normalized) - set(properties))
        if unexpected:
            raise ScriptValidationError(f"Unexpected parameters: {', '.join(unexpected)}")
    for name, value in normalized.items():
        rule = properties.get(name)
        if not rule:
            continue
        _validate_value(name, value, rule)
    return normalized


def _validate_value(name: str, value: Any, rule: dict[str, Any]) -> None:
    expected = rule.get("type")
    matches = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
        None: True,
    }
    if not matches.get(expected, False):
        raise ScriptValidationError(f"Parameter {name} has invalid type")
    if "enum" in rule and value not in rule["enum"]:
        raise ScriptValidationError(f"Parameter {name} is not an allowed value")
    if isinstance(value, str):
        if "minLength" in rule and len(value) < int(rule["minLength"]):
            raise ScriptValidationError(f"Parameter {name} is too short")
        if "maxLength" in rule and len(value) > int(rule["maxLength"]):
            raise ScriptValidationError(f"Parameter {name} is too long")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in rule and value < rule["minimum"]:
            raise ScriptValidationError(f"Parameter {name} is below minimum")
        if "maximum" in rule and value > rule["maximum"]:
            raise ScriptValidationError(f"Parameter {name} is above maximum")
    if isinstance(value, list):
        if "maxItems" in rule and len(value) > int(rule["maxItems"]):
            raise ScriptValidationError(f"Parameter {name} has too many items")
        item_rule = rule.get("items")
        if isinstance(item_rule, dict):
            for index, item in enumerate(value):
                _validate_value(f"{name}[{index}]", item, item_rule)
