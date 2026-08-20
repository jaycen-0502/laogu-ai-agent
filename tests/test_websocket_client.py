from pathlib import Path

from agent.server_client import CredentialStore, ServerClient


class FakeSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)

    def recv(self):
        if self.messages:
            return self.messages.pop(0)
        raise TimeoutError("timed out")

    def close(self):
        self.closed = True


def test_command_websocket_ack_result_and_close(tmp_path: Path):
    socket = FakeSocket([{"type": "command", "command": {"command_id": "c1", "command_type": "REFRESH_PROFILE", "profile_id": "p1"}}])
    store = CredentialStore(tmp_path / "credentials.json", protector=type("P", (), {"protect": lambda s, v: v, "unprotect": lambda s, v: v})(), test_mode=True)
    store.save({"agent_id": "a1", "agent_token": "secret", "server_url": "https://server.example"})
    captured = {}
    def factory(url, timeout, header):
        captured.update(url=url, timeout=timeout, header=header)
        return socket
    client = ServerClient("https://server.example", store, websocket_factory=factory)
    seen = []
    assert client.run_command_socket(lambda command: seen.append(command["command_id"]) or {"ok": True}) == 1
    assert seen == ["c1"]
    assert socket.closed is True
    assert any('"type": "hello"' in item for item in socket.sent)
    assert any('"type": "ack"' in item for item in socket.sent)
    assert any('"type": "result"' in item for item in socket.sent)
    assert captured["url"] == "wss://server.example/api/agent/commands/ws"
    assert captured["header"] == ["Authorization: Bearer secret"]
