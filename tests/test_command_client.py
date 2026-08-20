from __future__ import annotations

from pathlib import Path

from agent.server_client import CredentialStore, ServerClient


class FakeProtector:
    def protect(self, value: str) -> str:
        return value[::-1]

    def unprotect(self, value: str) -> str:
        return value[::-1]


def test_server_client_command_fallback_contract(tmp_path: Path):
    calls = []

    def transport(method, path, payload, token):
        calls.append((method, path, payload, token))
        if path.endswith("/pull"):
            return {"items": [{"command_id": "command-1", "status": "DELIVERED"}]}
        return {"ok": True}

    store = CredentialStore(tmp_path / "credentials.json", protector=FakeProtector(), test_mode=True)
    store.save({"agent_id": "agent-1", "agent_token": "agent-token", "server_url": "http://server"})
    client = ServerClient("http://server", store, transport=transport)
    assert client.pull_commands() == [{"command_id": "command-1", "status": "DELIVERED"}]
    client.acknowledge_command("command-1", "RUNNING")
    client.complete_command("command-1", "SUCCESS", {"ok": True})
    assert calls[0][1] == "/api/agent/commands/pull"
    assert calls[1][2]["status"] == "RUNNING"
    assert calls[2][2]["result"] == {"ok": True}
    assert all(call[3] == "agent-token" for call in calls)
