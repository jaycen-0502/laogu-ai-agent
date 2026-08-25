from sqlalchemy import select

from server.ai_service import AIService, AIUsageResult
from server.models import User
from tests.test_ai_providers import API_KEY, auth, create_provider, make_env


def test_translation_uses_assigned_user_model_and_redacts_source_from_audit(monkeypatch):
    env = make_env()
    item = create_provider(env, name="Translation Provider", is_default=True)
    with env["client"].app.state.SessionLocal() as db:
        member_id = db.scalar(select(User.id).where(User.username == "member-a"))
    policy = env["client"].put(
        f"/api/users/{member_id}/ai-policy",
        headers=auth(env["owner"]),
        json={"feature": "TRANSLATE", "enabled": True, "provider_id": item["provider_id"], "model": "gpt-test"},
    )
    assert policy.status_code == 200, policy.text
    calls = []

    def fake_stream(self, **kwargs):
        calls.append(kwargs)
        yield {"type": "delta", "delta": "你好，世界"}
        yield {"type": "completed", "usage": AIUsageResult(3, 4, 7)}

    monkeypatch.setattr(AIService, "stream", fake_stream)
    response = env["client"].post(
        "/api/ai/translate",
        headers=auth(env["member"]),
        json={"text": "Hello, world", "source_language": "en", "target_language": "zh-CN", "model": "not-allowed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["translated_text"] == "你好，世界"
    assert "model" not in response.json()
    assert calls and calls[0]["model"] == "gpt-test"
    audit_rows = env["client"].get("/api/audit", headers=auth(env["owner"])).json()
    assert all("Hello, world" not in row["message"] for row in audit_rows)
    assert API_KEY not in response.text


def test_translation_is_disabled_for_members_until_assigned(monkeypatch):
    env = make_env()

    def fake_stream(self, **kwargs):
        yield {"type": "delta", "delta": "should not run"}

    monkeypatch.setattr(AIService, "stream", fake_stream)
    response = env["client"].post(
        "/api/ai/translate",
        headers=auth(env["member"]),
        json={"text": "Hello", "source_language": "en", "target_language": "zh-CN"},
    )
    assert response.status_code == 403
