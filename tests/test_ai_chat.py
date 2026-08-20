from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.ai_service import AIRequestCancelled, AIRequestError, AIRequestTimeout, AIService, AIUsageResult, ChatRunHandle
from server.chat_api import sanitize_chat_content, trim_context
from server.config import ServerSettings
from server.main import create_app
from server.models import AIProvider, AIUsage, ChatMessage


API_KEY = "sk-stage9b-plain-secret-1234"
FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_env(database_url="sqlite://"):
    settings = ServerSettings(
        database_url=database_url,
        jwt_secret="ai-chat-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key=FERNET_KEY,
        ai_chat_max_context_messages=8,
        ai_chat_max_context_tokens=1000,
        rate_limit_ai_chat=100,
    )
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Studio A", "username": "admin", "password": "password123"},
    ).json()
    admin = boot["access_token"]
    for username, role in (("owner-a", "OWNER"), ("member-a", "MEMBER"), ("member-a2", "MEMBER")):
        client.post(
            "/api/users",
            headers=auth(admin),
            json={"username": username, "password": "password123", "role": role, "workspace_id": boot["workspace_id"]},
        )
    workspace_b = client.post("/api/workspaces", headers=auth(admin), json={"name": "Studio B"}).json()["id"]
    client.post(
        "/api/users",
        headers=auth(admin),
        json={"username": "owner-b", "password": "password123", "role": "OWNER", "workspace_id": workspace_b},
    )

    def login(username: str) -> str:
        return client.post("/api/auth/login", json={"username": username, "password": "password123"}).json()["access_token"]

    env = {
        "client": client,
        "admin": admin,
        "owner": login("owner-a"),
        "member": login("member-a"),
        "member2": login("member-a2"),
        "owner_b": login("owner-b"),
        "workspace_id": boot["workspace_id"],
        "workspace_b": workspace_b,
    }
    env["provider"] = create_provider(env, "owner", "OpenAI Main", is_default=True)
    return env


def create_provider(env, token_name: str, name: str, *, is_default=False, status="ENABLED"):
    response = env["client"].post(
        "/api/ai/providers",
        headers=auth(env[token_name]),
        json={
            "name": name,
            "provider_type": "OPENAI",
            "base_url": "",
            "api_key": API_KEY,
            "default_model": "gpt-test",
            "status": status,
            "is_default": is_default,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_session(env, token_name="member", **payload):
    response = env["client"].post(
        "/api/ai/chat/sessions",
        headers=auth(env[token_name]),
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def sse_events(response) -> list[tuple[str, dict]]:
    events = []
    for block in response.text.split("\n\n"):
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if event and data:
            events.append((event, json.loads(data)))
    return events


class QueueAIService:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        for part in output:
            yield {"type": "delta", "delta": part}
        yield {"type": "completed", "usage": AIUsageResult(11, 3, 14)}


def test_session_create_read_list_delete_and_member_access():
    env = make_env()
    session = create_session(env, system_prompt="你是一个简洁助手")
    assert session["title"] == "新聊天"
    assert session["model"] == "gpt-test"
    assert session["messages"][0]["role"] == "system"
    listed = env["client"].get("/api/ai/chat/sessions", headers=auth(env["member"])).json()
    assert listed["total"] == 1 and listed["items"][0]["session_id"] == session["session_id"]
    detail = env["client"].get(f"/api/ai/chat/sessions/{session['session_id']}", headers=auth(env["member"])).json()
    assert detail["user_id"] == session["user_id"]
    deleted = env["client"].delete(f"/api/ai/chat/sessions/{session['session_id']}", headers=auth(env["member"]))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert env["client"].get(f"/api/ai/chat/sessions/{session['session_id']}", headers=auth(env["member"])).status_code == 404


def test_streaming_message_multiturn_context_title_usage_and_audit():
    env = make_env()
    fake = QueueAIService([["O", "K"], ["You asked: ", "Reply with exactly: OK"]])
    env["client"].app.state.ai_service = fake
    session = create_session(env, system_prompt="Answer briefly")
    first = env["client"].post(
        f"/api/ai/chat/sessions/{session['session_id']}/messages",
        headers=auth(env["member"]),
        json={"content": "Reply with exactly: OK"},
    )
    assert first.status_code == 200
    assert [name for name, _ in sse_events(first)][-1] == "message.completed"
    second = env["client"].post(
        f"/api/ai/chat/sessions/{session['session_id']}/messages",
        headers=auth(env["member"]),
        json={"content": "What did I just ask you?"},
    )
    assert second.status_code == 200
    assert [item["role"] for item in fake.calls[1]["messages"]] == ["system", "user", "assistant", "user"]
    assert fake.calls[1]["messages"][-2]["content"] == "OK"
    detail = env["client"].get(f"/api/ai/chat/sessions/{session['session_id']}", headers=auth(env["member"])).json()
    assert detail["title"] == "Reply with exactly: OK"
    assert [item["status"] for item in detail["messages"][-4:]] == ["SUCCESS"] * 4
    assert detail["messages"][-1]["content"] == "You asked: Reply with exactly: OK"
    assert detail["usage"] == {"prompt_tokens": 22, "completion_tokens": 6, "total_tokens": 28, "latency_ms": detail["usage"]["latency_ms"]}
    with env["client"].app.state.SessionLocal() as db:
        rows = list(db.scalars(select(AIUsage).where(AIUsage.session_id == session["session_id"])))
        assert len(rows) == 2 and all(item.total_tokens == 14 for item in rows)
    actions = {item["action"] for item in env["client"].get("/api/audit", headers=auth(env["owner"])).json()}
    assert {"AI_CHAT_SESSION_CREATED", "AI_CHAT_COMPLETED"}.issubset(actions)


def test_user_workspace_provider_and_model_isolation():
    env = make_env()
    own = create_session(env)
    assert env["client"].get(f"/api/ai/chat/sessions/{own['session_id']}", headers=auth(env["member2"])).status_code == 404
    assert env["client"].get(f"/api/ai/chat/sessions/{own['session_id']}", headers=auth(env["owner_b"])).status_code == 404
    assert env["client"].get(f"/api/ai/chat/sessions/{own['session_id']}", headers=auth(env["admin"])).status_code == 404

    invalid_model = env["client"].post(
        "/api/ai/chat/sessions",
        headers=auth(env["member"]),
        json={"provider_id": env["provider"]["provider_id"], "model": "not-allowed"},
    )
    assert invalid_model.status_code == 422
    disabled = create_provider(env, "owner", "Disabled", status="DISABLED")
    assert env["client"].post(
        "/api/ai/chat/sessions",
        headers=auth(env["member"]),
        json={"provider_id": disabled["provider_id"], "model": "gpt-test"},
    ).status_code == 422

    other = create_provider(env, "owner_b", "Workspace B Provider", is_default=True)
    assert env["client"].post(
        "/api/ai/chat/sessions",
        headers=auth(env["member"]),
        json={"provider_id": other["provider_id"], "model": "gpt-test"},
    ).status_code == 422


def test_timeout_and_provider_failure_are_recorded_without_secret_leaks():
    env = make_env()
    session = create_session(env)
    fake = QueueAIService([AIRequestTimeout(f"timeout {API_KEY}"), AIRequestError(f"failed {API_KEY}")])
    env["client"].app.state.ai_service = fake
    for expected_code in ("TIMEOUT", "PROVIDER_ERROR"):
        response = env["client"].post(
            f"/api/ai/chat/sessions/{session['session_id']}/messages",
            headers=auth(env["member"]),
            json={"content": "test"},
        )
        assert response.status_code == 200 and API_KEY not in response.text
        assert sse_events(response)[-1][0] == "message.error"
        with env["client"].app.state.SessionLocal() as db:
            usage = db.scalars(select(AIUsage).where(AIUsage.session_id == session["session_id"]).order_by(AIUsage.created_at.desc())).first()
            assert usage.status == "FAILED" and usage.error_code == expected_code
    detail = env["client"].get(f"/api/ai/chat/sessions/{session['session_id']}", headers=auth(env["member"])).text
    assert API_KEY not in detail


def test_true_stop_cancels_server_request_and_concurrent_sessions_are_allowed(tmp_path):
    env = make_env(f"sqlite:///{(tmp_path / 'concurrent-chat.db').as_posix()}")
    first = create_session(env)
    second = create_session(env)

    class BlockingAIService:
        def __init__(self):
            self.started = 0
            self.lock = threading.Lock()
            self.ready = threading.Event()

        def stream(self, **kwargs):
            with self.lock:
                self.started += 1
                if self.started == 2:
                    self.ready.set()
            yield {"type": "delta", "delta": "partial"}
            while not kwargs["handle"].cancelled:
                time.sleep(0.01)
            raise AIRequestCancelled("cancelled")

    fake = BlockingAIService()
    env["client"].app.state.ai_service = fake
    responses = {}

    def run(item):
        responses[item["session_id"]] = env["client"].post(
            f"/api/ai/chat/sessions/{item['session_id']}/messages",
            headers=auth(env["member"]),
            json={"content": "long answer"},
        )

    threads = [threading.Thread(target=run, args=(item,)) for item in (first, second)]
    for thread in threads:
        thread.start()
    assert fake.ready.wait(3), "two chat sessions did not start concurrently"
    for item in (first, second):
        stopped = env["client"].post(f"/api/ai/chat/sessions/{item['session_id']}/stop", headers=auth(env["member"]))
        assert stopped.status_code == 200 and stopped.json()["stopped"] is True
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    for item in (first, second):
        events = sse_events(responses[item["session_id"]])
        assert events[-1][0] == "message.cancelled"
        detail = env["client"].get(f"/api/ai/chat/sessions/{item['session_id']}", headers=auth(env["member"])).json()
        assert detail["messages"][-1]["status"] == "CANCELLED"
        assert detail["messages"][-1]["content"] == "partial"
        assert detail["is_running"] is False


def test_sensitive_prompt_and_response_are_redacted_at_rest_and_in_api():
    env = make_env()
    raw_prompt_secret = "sk-user-super-secret-9999"
    raw_response_secret = "Bearer provider-secret-token-value"
    fake = QueueAIService([[f"Authorization: {raw_response_secret}"]])
    env["client"].app.state.ai_service = fake
    session = create_session(env)
    response = env["client"].post(
        f"/api/ai/chat/sessions/{session['session_id']}/messages",
        headers=auth(env["member"]),
        json={"content": f"my api_key={raw_prompt_secret}"},
    )
    assert raw_prompt_secret not in response.text and raw_response_secret not in response.text
    detail = env["client"].get(f"/api/ai/chat/sessions/{session['session_id']}", headers=auth(env["member"])).text
    assert raw_prompt_secret not in detail and raw_response_secret not in detail
    assert "[REDACTED]" in detail
    with env["client"].app.state.SessionLocal() as db:
        stored = " ".join(item.content for item in db.scalars(select(ChatMessage).where(ChatMessage.session_id == session["session_id"])))
        assert raw_prompt_secret not in stored and raw_response_secret not in stored


def test_context_trimming_preserves_system_and_most_recent_messages():
    messages = [{"role": "system", "content": "system rules"}]
    messages.extend({"role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index}-" + "x" * 80} for index in range(12))
    trimmed = trim_context(messages, max_messages=5, max_tokens=80)
    assert trimmed[0]["role"] == "system"
    assert trimmed[-1]["content"].startswith("message-11")
    assert len(trimmed) <= 5
    assert sum((len(item["content"]) + 3) // 4 + 4 for item in trimmed) <= 80


def test_chat_content_sanitizer_does_not_redact_normal_session_word():
    assert sanitize_chat_content("Please explain session management") == "Please explain session management"
    assert "secret-value" not in sanitize_chat_content("password: secret-value")


def test_openai_compatible_relay_falls_back_to_chat_completions(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, lines=()):
            self.status_code = status_code
            self.headers = {"content-type": "text/event-stream"}
            self.lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def iter_lines(self):
            yield from self.lines

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def close(self):
            pass

        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/responses"):
                return FakeResponse(404)
            return FakeResponse(200, (
                'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":null}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1,"total_tokens":5}}',
                "data: [DONE]",
            ))

    monkeypatch.setattr("server.ai_service.validate_provider_destination", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("server.ai_service.httpx.Client", FakeClient)
    events = list(AIService(30, 100, production=True).stream(
        base_url="https://relay.example.com/v1",
        api_key="relay-key",
        model="gpt-5.6-sol",
        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        handle=ChatRunHandle(),
    ))
    assert [item["type"] for item in events] == ["delta", "completed"]
    assert events[0]["delta"] == "OK" and events[1]["usage"].total_tokens == 5
    assert [call[1] for call in calls] == [
        "https://relay.example.com/v1/responses",
        "https://relay.example.com/v1/chat/completions",
    ]
    assert calls[1][2]["json"]["model"] == "gpt-5.6-sol"
    assert calls[1][2]["headers"]["Authorization"] == "Bearer relay-key"
