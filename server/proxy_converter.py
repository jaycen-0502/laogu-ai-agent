"""Small, dependency-free VLESS Reality YAML renderer.

The converter intentionally does not persist input and never logs the UUID or
Reality key material.  It only validates the shape needed by the supported
portable node template.
"""

from __future__ import annotations

import re


_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_SCALAR_RE = re.compile(r"^[A-Za-z0-9._:/+@-]+$")


def _quote(value: str) -> str:
    text = str(value)
    if text and _SAFE_SCALAR_RE.fullmatch(text) and text.lower() not in {"true", "false", "null", "~"}:
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_vless_reality_yaml(payload) -> str:
    server = str(payload.server).strip()
    if not _HOST_RE.fullmatch(server):
        raise ValueError("服务器地址包含不支持的字符")
    network = str(payload.network).strip().lower() or "tcp"
    if network not in {"tcp", "ws", "grpc", "h2"}:
        raise ValueError("network 仅支持 tcp、ws、grpc 或 h2")
    flow = str(getattr(payload, "flow", "") or "").strip()
    if len(flow) > 80 or any(char in flow for char in "\r\n"):
        raise ValueError("flow 参数格式不正确")
    fingerprint = str(payload.client_fingerprint).strip() or "chrome"
    lines = [
        "proxies:",
        f"  - name: {_quote(payload.name.strip())}",
        "    type: vless",
        f"    server: {_quote(server)}",
        f"    port: {int(payload.port)}",
        f"    uuid: {_quote(payload.uuid.strip())}",
        f"    udp: {'true' if payload.udp else 'false'}",
        f"    tls: {'true' if payload.tls else 'false'}",
        f"    network: {_quote(network)}",
    ]
    if flow:
        lines.append(f"    flow: {_quote(flow)}")
    lines.extend([
        f"    servername: {_quote(payload.servername.strip())}",
        "    reality-opts:",
        f"      public-key: {_quote(payload.public_key.strip())}",
        f"      short-id: {_quote(payload.short_id.strip())}",
        f"    client-fingerprint: {_quote(fingerprint)}",
    ])
    return "\n".join(lines) + "\n"
