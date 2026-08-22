from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from .device_identity import current_device_id
from common.release import VERSION


class ServerClientError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class DpapiProtector:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI is required for production credential storage")
        try:
            import win32crypt
        except ImportError as exc:
            raise RuntimeError("pywin32 is required for Windows DPAPI credential storage") from exc
        self.win32crypt = win32crypt

    def protect(self, value: str) -> str:
        encrypted = self.win32crypt.CryptProtectData(value.encode("utf-8"), "Laogu Agent Token", None, None, None, 0)
        return base64.b64encode(encrypted).decode("ascii")

    def unprotect(self, value: str) -> str:
        decrypted = self.win32crypt.CryptUnprotectData(base64.b64decode(value), None, None, None, 0)[1]
        return decrypted.decode("utf-8")


def protect_agent_directory(path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows ACL protection is required for production credentials")
    import ntsecuritycon
    import win32api
    import win32security

    path.mkdir(parents=True, exist_ok=True)
    process_token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    user_sid = win32security.GetTokenInformation(process_token, win32security.TokenUser)[0]
    system_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
    admins_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)
    dacl = win32security.ACL()
    flags = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
    for sid in (user_sid, system_sid, admins_sid):
        dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, flags, ntsecuritycon.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION, None, None, dacl, None)


class CredentialStore:
    def __init__(self, path: Path, *, protector=None, test_mode: bool = False):
        self.path = path
        self.protector = protector or DpapiProtector()
        self.test_mode = test_mode

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("agent_token"):
                raise RuntimeError("Plaintext Agent Token credential file is not allowed; re-register the Agent")
            credentials = {key: str(value) for key, value in payload.items() if value and key != "agent_token_protected"}
            if payload.get("agent_token_protected"):
                credentials["agent_token"] = self.protector.unprotect(str(payload["agent_token_protected"]))
            return credentials
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    def save(self, payload: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.test_mode:
            protect_agent_directory(self.path.parent)
        stored = {key: value for key, value in payload.items() if key != "agent_token"}
        token = str(payload.get("agent_token") or "")
        if token:
            stored["agent_token_protected"] = self.protector.protect(token)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


class ServerClient:
    def __init__(
        self,
        server_url: str,
        credential_store: CredentialStore,
        *,
        timeout_seconds: int = 15,
        transport=None,
        websocket_factory=None,
    ):
        self.server_url = server_url.rstrip("/")
        self.credential_store = credential_store
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.websocket_factory = websocket_factory
        self.credentials = credential_store.load()
        self.device_id = str(self.credentials.get("device_id") or current_device_id())
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def agent_id(self) -> str:
        return self.credentials.get("agent_id", "")

    def register(self, *, agent_name: str, machine_name: str, client_version: str, user_token: str) -> dict[str, Any]:
        result = self._request("POST", "/api/agents/register", {"agent_name": agent_name, "machine_name": machine_name, "client_version": client_version, "device_id": self.device_id}, token=user_token)
        self.credentials = {"agent_id": str(result["agent_id"]), "agent_token": str(result["agent_token"]), "server_url": self.server_url, "device_id": self.device_id}
        self.credential_store.save(self.credentials)
        return {key: value for key, value in result.items() if key != "agent_token"}

    def replace_agent_token(self, agent_id: str, agent_token: str) -> None:
        self.credentials = {"agent_id": str(agent_id), "agent_token": str(agent_token), "server_url": self.server_url, "device_id": self.device_id}
        self.credential_store.save(self.credentials)

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._agent_request("POST", "/api/agents/heartbeat", payload)

    def sync_accounts(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self._agent_request("POST", "/api/accounts/sync", {"agent_id": self.agent_id, "items": items})

    def pull_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        response = self._agent_request("POST", "/api/tasks/pull", {"agent_id": self.agent_id, "limit": limit})
        return response.get("items", [])

    def send_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._agent_request("POST", "/api/tasks/result", payload)

    def send_automation_metric(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._agent_request(
            "POST",
            "/api/agent/automation-metrics",
            {"agent_id": self.agent_id, **payload},
        )

    def fetch_task_script(self, task_id: str) -> dict[str, Any]:
        return self._agent_request("GET", f"/api/agent/tasks/{task_id}/script", None)

    def task_status(self, task_id: str) -> str:
        response = self._agent_request("GET", f"/api/agent/tasks/{task_id}/status", None)
        return str(response.get("status") or "UNKNOWN")

    def fetch_engine_manifest(self) -> dict[str, Any]:
        return self._agent_request("GET", "/api/agent/engine/manifest", None)

    def fetch_engine_source(self, source_url: str = "/api/agent/engine/source") -> bytes:
        # Do not follow a server-provided arbitrary URL with Agent credentials.
        # The manifest may name only this same-origin, fixed endpoint.
        if source_url != "/api/agent/engine/source":
            raise ServerClientError("Server returned an invalid engine source URL")
        if self.transport is not None:
            result = self.transport("GET_RAW", source_url, None, self.credentials.get("agent_token", ""))
            if not isinstance(result, bytes):
                raise ServerClientError("Server returned invalid engine source")
            return result
        return self._raw_agent_request(source_url)

    def pull_commands(self, limit: int = 10) -> list[dict[str, Any]]:
        response = self._agent_request("POST", "/api/agent/commands/pull", {"agent_id": self.agent_id, "limit": limit})
        items = response.get("items", [])
        return items if isinstance(items, list) else []

    def acknowledge_command(self, command_id: str, status: str = "ACKNOWLEDGED") -> dict[str, Any]:
        return self._agent_request(
            "POST",
            f"/api/agent/commands/{command_id}/ack",
            {"agent_id": self.agent_id, "status": status},
        )

    def complete_command(self, command_id: str, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        return self._agent_request(
            "POST",
            f"/api/agent/commands/{command_id}/result",
            {"agent_id": self.agent_id, "status": status, "result": result, "error": error},
        )

    def run_command_socket(self, handler, *, timeout_seconds: int = 3, stop_event=None, on_connected=None) -> int:
        """Receive and complete commands over WebSocket; raise to allow HTTP fallback."""
        token = self.credentials.get("agent_token", "")
        if not token:
            raise ServerClientError("Agent is not registered")
        try:
            factory = self.websocket_factory
            if factory is None:
                import websocket
                factory = websocket.create_connection
            parts = urlsplit(self.server_url)
            scheme = "wss" if parts.scheme == "https" else "ws"
            path = parts.path.rstrip("/") + "/api/agent/commands/ws"
            url = urlunsplit((scheme, parts.netloc, path, "", ""))
            socket = factory(url, timeout=timeout_seconds, header=[f"Authorization: Bearer {token}", f"X-Laogu-Device-ID: {self.device_id}"])
            if on_connected:
                on_connected()
        except Exception as exc:
            raise ServerClientError(f"WebSocket unavailable: {exc}") from exc
        completed = 0
        try:
            socket.send(json.dumps({"type": "hello", "agent_id": self.agent_id}))
            while True:
                try:
                    raw = socket.recv()
                except Exception as exc:
                    name = type(exc).__name__.lower()
                    if "timeout" in name:
                        if stop_event is not None and not stop_event.is_set():
                            continue
                        break
                    raise ServerClientError(f"WebSocket receive failed: {exc}") from exc
                if not raw:
                    break
                try:
                    message = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, json.JSONDecodeError):
                    continue
                command = message.get("command") if isinstance(message, dict) and message.get("type") == "command" else None
                if not isinstance(command, dict):
                    continue
                command_id = str(command.get("command_id") or "")
                if not command_id:
                    continue
                socket.send(json.dumps({"type": "ack", "command_id": command_id, "status": "RUNNING"}))
                try:
                    result = handler(command)
                except Exception as exc:
                    payload = {"type": "result", "command_id": command_id, "status": "FAILED", "error": str(exc)[:500]}
                    socket.send(json.dumps(payload))
                else:
                    socket.send(json.dumps({"type": "result", "command_id": command_id, "status": "SUCCESS", "result": result or {}}))
                completed += 1
        finally:
            try:
                socket.close()
            except Exception:
                pass
        return completed

    def _agent_request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        token = self.credentials.get("agent_token", "")
        if not token:
            raise ServerClientError("Agent is not registered")
        return self._request(method, path, payload, token=token)

    def _raw_agent_request(self, path: str) -> bytes:
        token = self.credentials.get("agent_token", "")
        if not token:
            raise ServerClientError("Agent is not registered")
        headers = {
            "Accept": "text/x-python",
            "Authorization": f"Bearer {token}",
            "User-Agent": f"Laogu-Desktop-Agent/{VERSION} (Windows; HTTPS)",
            "X-Laogu-Client": "desktop-agent",
            "X-Laogu-Device-ID": self.device_id,
        }
        request = urllib.request.Request(self.server_url + path, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                payload = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ServerClientError(f"Server returned HTTP {exc.code}: {detail[:300]}", status_code=exc.code) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ServerClientError(f"Server unavailable: {exc}") from exc
        if not payload or len(payload) > 2 * 1024 * 1024:
            raise ServerClientError("Server returned invalid engine source")
        return payload

    def _request(self, method: str, path: str, payload: dict[str, Any] | None, *, token: str = "") -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(method, path, payload, token)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": f"Laogu-Desktop-Agent/{VERSION} (Windows; HTTPS)",
            "X-Laogu-Client": "desktop-agent",
        }
        if token:
            headers["X-Laogu-Device-ID"] = self.device_id
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.server_url + path, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ServerClientError(f"Server returned HTTP {exc.code}: {detail[:300]}", status_code=exc.code) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ServerClientError(f"Server unavailable: {exc}") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ServerClientError("Server returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ServerClientError("Server response must be an object")
        return decoded
