from __future__ import annotations

from sqlalchemy import select
from fastapi.testclient import TestClient

from server.ai_service import AIRequestTimeout, AIUsageResult
from server.analysis_service import AIAnalysisRunResult, parse_analysis_json, sanitize_analysis_text
from server.config import ServerSettings
from server.main import create_app
from server.models import Account, Activity, AIAnalysis, AuditLog, now


FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class QueueAnalysisService:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return AIAnalysisRunResult(
            result=output,
            summary=str(output.get("overview") or ""),
            usage=AIUsageResult(11, 5, 16),
        )


def make_env():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="ai-analysis-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key=FERNET_KEY,
        rate_limit_ai_analysis=100,
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
            "name": "Analysis Provider",
            "provider_type": "OPENAI",
            "base_url": "",
            "api_key": "sk-analysis-secret-1234",
            "default_model": "gpt-test",
            "status": "ENABLED",
            "is_default": True,
        },
    )
    assert provider.status_code == 200, provider.text
    with client.app.state.SessionLocal() as db:
        account = Account(
            workspace_id=boot["workspace_id"],
            agent_id="agent-a",
            profile_id="profile-a",
            x_username="@example",
            x_account_id="x-a",
            login_status="LOGGED_IN",
            browser_status="RUNNING",
            account_status="VALID",
            last_checked=now(),
        )
        db.add(account)
        db.flush()
        for index, status in enumerate(("SUCCESS", "FAILED")):
            db.add(Activity(
                workspace_id=boot["workspace_id"],
                agent_id="agent-a",
                profile_id="profile-a",
                x_account_id="x-a",
                task_id=f"task-{index}",
                activity_type="script.execute",
                status=status,
                duration=2 + index,
                summary=f"brand automation activity {index}",
                result={"label": "brand"},
                timestamp=now(),
            ))
        db.commit()
        account_id = account.id
    return {
        "client": client,
        "admin": admin,
        "member": login("member-a"),
        "member2": login("member-a2"),
        "workspace_id": boot["workspace_id"],
        "provider": provider.json(),
        "account_id": account_id,
    }


def test_account_analysis_snapshot_usage_history_and_audit():
    env = make_env()
    fake = QueueAnalysisService([{
        "overview": "账号运行基本正常，但样本不足。",
        "health_score": 72,
        "data_quality": {"level": "LOW", "warnings": ["样本少于10条"]},
        "strengths": ["已登录"],
        "risks": ["有失败活动"],
        "recommendations": ["继续采集"],
    }])
    env["client"].app.state.ai_analysis_service = fake
    response = env["client"].post(
        "/api/ai/analysis/account",
        headers=auth(env["member"]),
        json={"account_id": env["account_id"], "provider_id": env["provider"]["provider_id"], "lookback_days": 30},
    )
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["status"] == "SUCCESS" and item["analysis_type"] == "ACCOUNT"
    assert item["source_snapshot"]["activity_metrics"]["sample_size"] == 2
    assert item["source_snapshot"]["activity_metrics"]["success_rate"] == 0.5
    assert item["total_tokens"] == 16 and item["result"]["health_score"] == 72
    listed = env["client"].get("/api/ai/analysis", headers=auth(env["member"])).json()
    assert listed["total"] == 1 and listed["items"][0]["analysis_id"] == item["analysis_id"]
    assert env["client"].get(f"/api/ai/analysis/{item['analysis_id']}", headers=auth(env["member2"])).status_code == 404
    with env["client"].app.state.SessionLocal() as db:
        audit_row = db.scalars(select(AuditLog).where(AuditLog.action == "AI_ACCOUNT_ANALYZED")).first()
        assert audit_row and audit_row.result == "SUCCESS"


def test_keyword_analysis_deterministic_counts_redaction_and_delete():
    env = make_env()
    fake = QueueAnalysisService([{
        "overview": "brand出现两次。",
        "data_quality": {"level": "LOW", "warnings": []},
        "keyword_findings": [],
        "themes": [],
        "risks": [],
        "recommendations": [],
    }])
    env["client"].app.state.ai_analysis_service = fake
    secret = "sk-user-secret-999999"
    response = env["client"].post(
        "/api/ai/analysis/keywords",
        headers=auth(env["member"]),
        json={
            "provider_id": env["provider"]["provider_id"],
            "keywords": ["brand", " BRAND ", "missing"],
            "input_text": f"Brand is useful. brand is growing. api_key={secret}",
            "title": "Brand check",
        },
    )
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["keywords"] == ["brand", "missing"]
    assert item["source_snapshot"]["keyword_counts"] == {"brand": 2, "missing": 0}
    assert secret not in response.text
    assert secret not in str(fake.calls[0]["messages"])
    deleted = env["client"].delete(f"/api/ai/analysis/{item['analysis_id']}", headers=auth(env["member"]))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert env["client"].get(f"/api/ai/analysis/{item['analysis_id']}", headers=auth(env["member"])).status_code == 404


def test_keyword_requires_text_or_activity_and_failure_is_recorded():
    env = make_env()
    missing = env["client"].post(
        "/api/ai/analysis/keywords",
        headers=auth(env["member"]),
        json={"provider_id": env["provider"]["provider_id"], "keywords": ["brand"]},
    )
    assert missing.status_code == 422
    env["client"].app.state.ai_analysis_service = QueueAnalysisService([AIRequestTimeout("secret timeout")])
    failed = env["client"].post(
        "/api/ai/analysis/keywords",
        headers=auth(env["member"]),
        json={"provider_id": env["provider"]["provider_id"], "keywords": ["brand"], "input_text": "brand"},
    )
    assert failed.status_code == 504 and failed.json()["detail"] == "Internal server error"
    with env["client"].app.state.SessionLocal() as db:
        row = db.scalars(select(AIAnalysis).order_by(AIAnalysis.created_at.desc())).first()
        assert row.status == "FAILED" and row.error_code == "TIMEOUT"
        audit_row = db.scalars(select(AuditLog).where(AuditLog.action == "AI_ANALYSIS_FAILED")).first()
        assert audit_row and audit_row.result == "FAILED"


def test_analysis_json_parser_and_sanitizer():
    parsed = parse_analysis_json('```json\n{"overview":"ok"}\n```')
    assert parsed == {"overview": "ok"}
    assert "secret-value" not in sanitize_analysis_text("password: secret-value")
