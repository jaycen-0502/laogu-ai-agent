from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.ai_service import AIRequestTimeout, AIUsageResult
from server.analysis_service import AIAnalysisRunResult
from server.config import ServerSettings
from server.main import create_app
from server.models import AIWritingRecord, AuditLog
from server.writing_service import AIWritingService


FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class QueueWritingService:
    def __init__(self, analyses=(), replies=()):
        self.analyses = list(analyses)
        self.replies = list(replies)
        self.calls = []

    def analyze(self, **kwargs):
        self.calls.append(("ANALYSIS", kwargs))
        output = self.analyses.pop(0)
        if isinstance(output, Exception):
            raise output
        return AIAnalysisRunResult(output, str(output.get("overview") or ""), AIUsageResult(10, 4, 14))

    def generate(self, **kwargs):
        self.calls.append(("REPLY", kwargs))
        output = self.replies.pop(0)
        if isinstance(output, Exception):
            raise output
        return AIAnalysisRunResult(output, str(output.get("overview") or ""), AIUsageResult(12, 8, 20))


def make_env():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="ai-writing-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key=FERNET_KEY,
        rate_limit_ai_writing=100,
    )
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post(
        "/api/auth/bootstrap",
        json={"workspace_name": "Studio A", "username": "admin", "password": "password123"},
    ).json()
    admin = boot["access_token"]
    for username in ("member-a", "member-a2"):
        response = client.post(
            "/api/users",
            headers=auth(admin),
            json={"username": username, "password": "password123", "role": "MEMBER", "workspace_id": boot["workspace_id"]},
        )
        assert response.status_code == 200

    def login(username: str) -> str:
        return client.post("/api/auth/login", json={"username": username, "password": "password123"}).json()["access_token"]

    provider = client.post(
        "/api/ai/providers",
        headers=auth(admin),
        json={
            "name": "Writing Provider",
            "provider_type": "OPENAI",
            "base_url": "",
            "api_key": "sk-writing-provider-1234",
            "default_model": "gpt-test",
            "status": "ENABLED",
            "is_default": True,
        },
    )
    assert provider.status_code == 200, provider.text
    return {
        "client": client,
        "member": login("member-a"),
        "member2": login("member-a2"),
        "provider": provider.json(),
    }


def test_writing_analysis_reply_history_isolation_and_audit():
    env = make_env()
    fake = QueueWritingService(
        analyses=[{
            "overview": "用户在询问退款进度。",
            "intent": "查询进度",
            "sentiment": "焦虑",
            "tone": "直接",
            "key_points": ["退款"],
            "risks": [],
            "reply_strategy": ["确认问题"],
            "data_quality": {"level": "HIGH", "warnings": []},
        }],
        replies=[{
            "overview": "已生成两条人工审核草稿。",
            "strategy": "先致歉，再邀请提供订单号。",
            "replies": [
                {"text": "很抱歉让您久等了，请私信提供订单号，我们马上帮您核查。", "tone": "友好", "reason": "明确下一步"},
                {"text": "理解您的着急。请通过私信发送订单号，我们会尽快查询退款进度。", "tone": "专业", "reason": "避免公开隐私"},
            ],
            "safety_notes": ["不要公开订单号"],
        }],
    )
    env["client"].app.state.ai_writing_service = fake
    analyzed = env["client"].post(
        "/api/ai/writing/analyze",
        headers=auth(env["member"]),
        json={"provider_id": env["provider"]["provider_id"], "source_text": "我的退款为什么还没到账？"},
    )
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["record_type"] == "ANALYSIS" and analyzed.json()["total_tokens"] == 14
    generated = env["client"].post(
        "/api/ai/writing/replies",
        headers=auth(env["member"]),
        json={
            "provider_id": env["provider"]["provider_id"],
            "source_text": "我的退款为什么还没到账？",
            "objective": "邀请用户私信订单号",
            "variant_count": 2,
            "max_characters": 120,
        },
    )
    assert generated.status_code == 200, generated.text
    item = generated.json()
    assert item["record_type"] == "REPLY" and item["total_tokens"] == 20
    assert item["parameters"]["requires_human_review"] is True
    assert item["parameters"]["auto_publish"] is False
    assert len(item["result"]["replies"]) == 2
    listed = env["client"].get("/api/ai/writing", headers=auth(env["member"])).json()
    assert listed["total"] == 2
    assert env["client"].get(f"/api/ai/writing/{item['record_id']}", headers=auth(env["member2"])).status_code == 404
    with env["client"].app.state.SessionLocal() as db:
        actions = {row.action for row in db.scalars(select(AuditLog).where(AuditLog.action.like("AI_%")))}
        assert {"AI_WRITING_ANALYZED", "AI_REPLIES_GENERATED"}.issubset(actions)


def test_writing_secrets_are_redacted_delete_and_failure_recorded():
    env = make_env()
    secret = "sk-user-secret-999999"
    fake = QueueWritingService(
        analyses=[{"overview": "正常", "intent": "咨询"}],
        replies=[AIRequestTimeout("provider timeout secret")],
    )
    env["client"].app.state.ai_writing_service = fake
    analyzed = env["client"].post(
        "/api/ai/writing/analyze",
        headers=auth(env["member"]),
        json={"provider_id": env["provider"]["provider_id"], "source_text": f"api_key={secret} 请分析"},
    )
    assert analyzed.status_code == 200 and secret not in analyzed.text
    assert secret not in fake.calls[0][1]["source_text"]
    record_id = analyzed.json()["record_id"]
    deleted = env["client"].delete(f"/api/ai/writing/{record_id}", headers=auth(env["member"]))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    failed = env["client"].post(
        "/api/ai/writing/replies",
        headers=auth(env["member"]),
        json={"provider_id": env["provider"]["provider_id"], "source_text": "需要回复", "variant_count": 1},
    )
    assert failed.status_code == 504
    with env["client"].app.state.SessionLocal() as db:
        row = db.scalars(select(AIWritingRecord).order_by(AIWritingRecord.created_at.desc())).first()
        assert row.status == "FAILED" and row.error_code == "TIMEOUT"


def test_writing_service_enforces_reply_count_length_deduplication():
    class RawAnalysisService:
        def run(self, **_kwargs):
            long_text = "a" * 80
            return AIAnalysisRunResult(
                result={
                    "overview": "drafts",
                    "strategy": "safe",
                    "replies": [
                        {"text": long_text, "tone": "friendly", "reason": "one"},
                        {"text": long_text, "tone": "friendly", "reason": "duplicate"},
                        {"text": "second reply", "tone": "professional", "reason": "two"},
                    ],
                    "safety_notes": ["review"],
                },
                summary="drafts",
                usage=AIUsageResult(1, 2, 3),
            )

    output = AIWritingService(RawAnalysisService()).generate(
        base_url="",
        api_key="key",
        model="model",
        source_text="source",
        context_text="",
        parameters={"tone": "FRIENDLY", "language": "AUTO", "variant_count": 2, "max_characters": 40},
    )
    assert len(output.result["replies"]) == 2
    assert output.result["replies"][0]["character_count"] == 40
    assert output.result["replies"][1]["text"] == "second reply"


def test_writing_validation_rejects_invalid_limits():
    env = make_env()
    response = env["client"].post(
        "/api/ai/writing/replies",
        headers=auth(env["member"]),
        json={"provider_id": env["provider"]["provider_id"], "source_text": "hello", "variant_count": 6, "max_characters": 20},
    )
    assert response.status_code == 422
