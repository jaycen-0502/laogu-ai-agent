from __future__ import annotations

from sqlalchemy import func, select
from server.ai_service import AIUsageResult
from server.analysis_service import AIAnalysisRunResult
from server.config import ServerSettings
from server.main import create_app
from server.models import Agent, AITaskProposal, AuditLog, Profile, Script, ScriptVersion, Task
from fastapi.testclient import TestClient


FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class QueueProposalService:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return AIAnalysisRunResult(self.result, self.result.get("summary", ""), AIUsageResult(11, 7, 18))


def make_env():
    settings = ServerSettings(
        database_url="sqlite://",
        jwt_secret="ai-task-test-secret-more-than-32-bytes",
        jwt_expire_minutes=60,
        agent_offline_seconds=90,
        ai_credential_key=FERNET_KEY,
        rate_limit_ai_task_proposal=100,
    )
    client = TestClient(create_app(settings.database_url, settings))
    boot = client.post("/api/auth/bootstrap", json={"workspace_name": "Studio A", "username": "admin", "password": "password123"}).json()
    token = boot["access_token"]
    provider = client.post(
        "/api/ai/providers", headers=auth(token), json={
            "name": "Task Provider", "provider_type": "OPENAI", "base_url": "", "api_key": "sk-task-provider-1234",
            "default_model": "gpt-test", "status": "ENABLED", "is_default": True,
        },
    )
    assert provider.status_code == 200, provider.text
    with client.app.state.SessionLocal() as db:
        agent = Agent(workspace_id=boot["workspace_id"], agent_name="Agent A", machine_name="machine-a", client_version="1.0")
        db.add(agent); db.flush()
        db.add(Profile(workspace_id=boot["workspace_id"], agent_id=agent.id, profile_id="profile-a", x_username="@demo"))
        script = Script(workspace_id=boot["workspace_id"], name="Example script", description="safe test", status="ENABLED", current_version=1, created_by=boot["user_id"])
        db.add(script); db.flush()
        version = ScriptVersion(script_id=script.id, version=1, source="module.exports.run = async () => ({ok:true})", params_schema={"type": "object", "properties": {"label": {"type": "string", "maxLength": 20}}, "additionalProperties": False}, sha256="a" * 64, created_by=boot["user_id"])
        db.add(version); db.commit()
        script_id, version_id = script.id, version.id
    return {"client": client, "token": token, "provider": provider.json(), "workspace_id": boot["workspace_id"], "script_id": script_id, "version_id": version_id}


def test_proposal_is_draft_before_confirmation_and_creates_task_after_confirmation():
    env = make_env()
    fake = QueueProposalService({
        "summary": "访问示例页面", "script_id": env["script_id"], "script_version_id": env["version_id"],
        "profile_ids": ["profile-a"], "params": {"label": "smoke"}, "timeout": 300,
        "reason": "使用已启用脚本", "risk_notes": ["请确认目标页面"], "needs_confirmation": True,
    })
    env["client"].app.state.ai_task_proposal_service = fake
    created = env["client"].post("/api/ai/task-proposals", headers=auth(env["token"]), json={"provider_id": env["provider"]["provider_id"], "request_text": "访问示例页面并记录标题", "timeout": 90})
    assert created.status_code == 200, created.text
    proposal = created.json()
    assert proposal["status"] == "DRAFT" and proposal["task_ids"] == []
    with env["client"].app.state.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    confirmed = env["client"].post(f"/api/ai/task-proposals/{proposal['proposal_id']}/confirm", headers=auth(env["token"]))
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "CONFIRMED" and len(body["task_ids"]) == 1
    task = body["tasks"][0]
    assert task["task_type"] == "script.execute" and task["script_version_id"] == env["version_id"]
    assert task["params"]["timeout"] == 90 and task["params"]["params"] == {"label": "smoke"}
    with env["client"].app.state.SessionLocal() as db:
        actions = {row.action for row in db.scalars(select(AuditLog))}
        assert {"AI_TASK_PROPOSED", "AI_TASK_CONFIRMED"}.issubset(actions)


def test_proposal_rejects_forged_plan_and_rejected_proposal_cannot_confirm():
    env = make_env()
    fake = QueueProposalService({
        "summary": "计划", "script_id": "forged-script", "script_version_id": "forged-version",
        "profile_ids": ["profile-a"], "params": {}, "needs_confirmation": True,
    })
    env["client"].app.state.ai_task_proposal_service = fake
    response = env["client"].post("/api/ai/task-proposals", headers=auth(env["token"]), json={"provider_id": env["provider"]["provider_id"], "request_text": "执行任务"})
    assert response.status_code == 200
    proposal_id = response.json()["proposal_id"]
    rejected = env["client"].post(f"/api/ai/task-proposals/{proposal_id}/reject", headers=auth(env["token"]))
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert env["client"].post(f"/api/ai/task-proposals/{proposal_id}/confirm", headers=auth(env["token"])).status_code == 409
    with env["client"].app.state.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_invalid_params_are_rejected_without_task_creation():
    env = make_env()
    env["client"].app.state.ai_task_proposal_service = QueueProposalService({
        "summary": "非法参数", "script_id": env["script_id"], "script_version_id": env["version_id"],
        "profile_ids": ["profile-a"], "params": {"unexpected": "value"}, "needs_confirmation": True,
    })
    response = env["client"].post("/api/ai/task-proposals", headers=auth(env["token"]), json={"provider_id": env["provider"]["provider_id"], "request_text": "执行"})
    assert response.status_code == 200
    confirmed = env["client"].post(f"/api/ai/task-proposals/{response.json()['proposal_id']}/confirm", headers=auth(env["token"]))
    assert confirmed.status_code == 422
    with env["client"].app.state.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
