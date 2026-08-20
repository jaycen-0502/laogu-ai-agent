from __future__ import annotations

import time
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.script_validation import ScriptValidationError, validate_script_params

from .ai_provider import CredentialCipher, CredentialError
from .ai_service import AIRequestError, AIRequestTimeout
from .analysis_service import AIAnalysisRunResult, sanitize_analysis_text
from .models import Agent, Profile, Script, ScriptVersion, Task, AIProvider, AITaskProposal, User, now
from .schemas import AITaskProposalCreate
from .security import audit
from .task_proposal_service import AITaskProposalService


PROPOSAL_STATUSES = frozenset({"PENDING", "DRAFT", "CONFIRMED", "REJECTED", "FAILED"})


def _dt(value):
    return value.isoformat() if value else None


def _proposal_dict(item: AITaskProposal, provider_name: str = "", *, detail: bool = False) -> dict:
    payload = {
        "proposal_id": item.id,
        "workspace_id": item.workspace_id,
        "user_id": item.user_id,
        "provider_id": item.provider_id,
        "provider_name": provider_name,
        "model": item.model,
        "status": item.status,
        "summary": item.summary,
        "plan": item.plan or {},
        "task_ids": item.task_ids or [],
        "prompt_tokens": item.prompt_tokens,
        "completion_tokens": item.completion_tokens,
        "total_tokens": item.total_tokens,
        "latency_ms": item.latency_ms,
        "error_code": item.error_code,
        "error": item.error,
        "created_at": _dt(item.created_at),
        "completed_at": _dt(item.completed_at),
    }
    if detail:
        payload.update({"request_text": item.request_text, "result": item.result or {}})
    return payload


def register_task_proposal_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    paged: Callable,
    cipher: CredentialCipher,
    proposal_service: AITaskProposalService,
    task_serializer: Callable,
) -> None:
    app.state.ai_task_proposal_service = proposal_service

    def visible_proposal(proposal_id: str, user: User, db: Session) -> AITaskProposal:
        item = db.get(AITaskProposal, proposal_id)
        if not item or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="AI task proposal not found")
        return item

    def checked_provider(db: Session, workspace_id: str, provider_id: str | None, model: str | None) -> tuple[AIProvider, str]:
        query = select(AIProvider).where(AIProvider.workspace_id == workspace_id, AIProvider.status == "ENABLED")
        query = query.where(AIProvider.id == provider_id) if provider_id else query.where(AIProvider.is_default.is_(True))
        provider = db.scalar(query)
        if not provider:
            raise HTTPException(status_code=422, detail="Enabled AI provider not found for this workspace")
        selected_model = str(model or provider.default_model or "").strip()
        if not selected_model:
            raise HTTPException(status_code=422, detail="AI model is required")
        allowed = set(provider.available_models or [])
        if provider.default_model:
            allowed.add(provider.default_model)
        if allowed and selected_model not in allowed:
            raise HTTPException(status_code=422, detail="AI model is not allowed for this provider")
        return provider, selected_model

    def catalog(db: Session, workspace_id: str) -> tuple[list[dict], list[dict]]:
        scripts: list[dict] = []
        for script in db.scalars(select(Script).where(Script.workspace_id == workspace_id, Script.status == "ENABLED").order_by(Script.name)):
            version = db.scalar(select(ScriptVersion).where(ScriptVersion.script_id == script.id, ScriptVersion.version == script.current_version))
            if version:
                scripts.append({
                    "script_id": script.id,
                    "name": script.name,
                    "description": script.description,
                    "script_version_id": version.id,
                    "version": version.version,
                    "params_schema": version.params_schema or {},
                })
        profiles = [
            {
                "profile_id": profile.profile_id,
                "x_username": profile.x_username,
                "x_account_id": profile.x_account_id,
                "agent_id": profile.agent_id,
            }
            for profile in db.scalars(select(Profile).where(Profile.workspace_id == workspace_id).order_by(Profile.profile_id))
        ]
        return scripts, profiles

    def execute_proposal(item: AITaskProposal, provider: AIProvider, model: str, request: Request, user: User, db: Session, request_text: str, scripts: list[dict], profiles: list[dict], timeout: int) -> dict:
        started = time.monotonic()
        http_error: HTTPException | None = None
        try:
            api_key = cipher.decrypt(provider.api_key_encrypted)
            output: AIAnalysisRunResult = app.state.ai_task_proposal_service.run(
                base_url=provider.base_url,
                api_key=api_key,
                model=model,
                request_text=request_text,
                scripts=scripts,
                profiles=profiles,
            )
            plan = dict(output.result)
            plan["timeout"] = timeout
            selected_script = next((value for value in scripts if value["script_id"] == plan.get("script_id") and value["script_version_id"] == plan.get("script_version_id")), None)
            profile_map = {value["profile_id"]: value for value in profiles}
            plan["script_name"] = selected_script["name"] if selected_script else ""
            plan["script_version"] = selected_script["version"] if selected_script else None
            plan["profile_labels"] = [
                {"profile_id": profile_id, "x_username": profile_map.get(profile_id, {}).get("x_username", "")}
                for profile_id in plan.get("profile_ids", [])
            ]
            plan["catalog_match"] = bool(selected_script) and all(profile_id in profile_map for profile_id in plan.get("profile_ids", []))
            item.plan = plan
            item.result = plan
            item.summary = output.summary
            item.status = "DRAFT"
            item.prompt_tokens = output.usage.prompt_tokens
            item.completion_tokens = output.usage.completion_tokens
            item.total_tokens = output.usage.total_tokens
        except CredentialError:
            item.status = "FAILED"; item.error_code = "CREDENTIAL_ERROR"; item.error = "AI凭据不可用"; http_error = HTTPException(status_code=503, detail=item.error)
        except AIRequestTimeout:
            item.status = "FAILED"; item.error_code = "TIMEOUT"; item.error = "AI任务规划请求超时"; http_error = HTTPException(status_code=504, detail=item.error)
        except AIRequestError:
            item.status = "FAILED"; item.error_code = "PROVIDER_ERROR"; item.error = "AI任务规划服务请求失败"; http_error = HTTPException(status_code=502, detail=item.error)
        except Exception:
            item.status = "FAILED"; item.error_code = "INTERNAL_ERROR"; item.error = "AI任务规划服务暂时不可用"; http_error = HTTPException(status_code=502, detail=item.error)
        item.latency_ms = max(0, round((time.monotonic() - started) * 1000)); item.completed_at = now()
        db.commit()
        audit(db, request, action="AI_TASK_PROPOSED" if item.status == "DRAFT" else "AI_TASK_PROPOSAL_FAILED", result=item.status, user_id=user.id, workspace_id=item.workspace_id, resource_type="ai_task_proposal", resource_id=item.id, message=item.error)
        if http_error:
            raise http_error
        return _proposal_dict(item, provider.name, detail=True)

    @app.get("/api/ai/task-proposals")
    def list_proposals(
        status: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(AITaskProposal).where(AITaskProposal.user_id == user.id)
        if status.strip():
            normalized = status.strip().upper()
            if normalized not in PROPOSAL_STATUSES:
                raise HTTPException(status_code=422, detail="Invalid proposal status")
            query = query.where(AITaskProposal.status == normalized)
        query = query.order_by(AITaskProposal.created_at.desc())

        def serialize(item: AITaskProposal) -> dict:
            provider = db.get(AIProvider, item.provider_id)
            return _proposal_dict(item, provider.name if provider else "")

        return paged(db, query, serialize, page=page, page_size=page_size)

    @app.get("/api/ai/task-proposals/{proposal_id}")
    def get_proposal(proposal_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_proposal(proposal_id, user, db)
        provider = db.get(AIProvider, item.provider_id)
        return _proposal_dict(item, provider.name if provider else "", detail=True)

    @app.post("/api/ai/task-proposals")
    def create_proposal(body: AITaskProposalCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not user.workspace_id:
            raise HTTPException(status_code=422, detail="User workspace is required")
        provider, model = checked_provider(db, user.workspace_id, body.provider_id, body.model)
        scripts, profiles = catalog(db, user.workspace_id)
        if not scripts:
            raise HTTPException(status_code=422, detail="No enabled scripts are available")
        request_text = sanitize_analysis_text(body.request_text)
        item = AITaskProposal(workspace_id=user.workspace_id, user_id=user.id, provider_id=provider.id, model=model, status="PENDING", request_text=request_text, summary="", plan={}, result={}, task_ids=[], created_at=now())
        db.add(item); db.commit()
        return execute_proposal(item, provider, model, request, user, db, request_text, scripts, profiles, body.timeout)

    @app.post("/api/ai/task-proposals/{proposal_id}/confirm")
    def confirm_proposal(proposal_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_proposal(proposal_id, user, db)
        if item.status != "DRAFT":
            raise HTTPException(status_code=409, detail="Only a draft proposal can be confirmed")
        plan = item.plan or {}
        if not plan.get("needs_confirmation"):
            raise HTTPException(status_code=422, detail="This proposal is not confirmed as executable")
        script_id = str(plan.get("script_id") or "").strip()
        version_id = str(plan.get("script_version_id") or "").strip()
        script = db.get(Script, script_id)
        version = db.get(ScriptVersion, version_id)
        if not script or script.workspace_id != item.workspace_id or script.status != "ENABLED":
            raise HTTPException(status_code=409, detail="Proposed script is missing or disabled")
        if not version or version.script_id != script.id:
            raise HTTPException(status_code=409, detail="Proposed script version is invalid")
        try:
            params = validate_script_params(plan.get("params") if isinstance(plan.get("params"), dict) else {}, version.params_schema or {})
        except ScriptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        profile_ids = list(dict.fromkeys(str(value).strip() for value in plan.get("profile_ids", []) if str(value).strip()))
        if not profile_ids or len(profile_ids) > 100:
            raise HTTPException(status_code=422, detail="At least one valid Profile is required")
        routes: list[tuple[Profile, Agent]] = []
        for profile_id in profile_ids:
            matches = list(db.scalars(select(Profile).where(Profile.workspace_id == item.workspace_id, Profile.profile_id == profile_id)))
            if len(matches) != 1:
                raise HTTPException(status_code=404 if not matches else 409, detail="Profile not found" if not matches else "Profile is ambiguous")
            profile = matches[0]; agent = db.get(Agent, profile.agent_id)
            if not agent or agent.workspace_id != item.workspace_id:
                raise HTTPException(status_code=409, detail="Profile agent routing is invalid")
            routes.append((profile, agent))
        timeout = min(300, max(1, int(plan.get("timeout") or 60)))
        tasks: list[Task] = []
        for profile, _agent in routes:
            task = Task(workspace_id=item.workspace_id, agent_id=profile.agent_id, profile_id=profile.profile_id, x_account_id=profile.x_account_id, script_id=script.id, script_version_id=version.id, task_type="script.execute", params={"script_id": script.id, "script_version_id": version.id, "params": params, "timeout": timeout}, timeout=timeout)
            db.add(task); db.flush(); tasks.append(task)
        item.status = "CONFIRMED"; item.task_ids = [task.id for task in tasks]; item.completed_at = now(); db.commit()
        for task, (_profile, agent) in zip(tasks, routes):
            audit(db, request, action="AI_TASK_CONFIRMED", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, agent_id=agent.id, profile_id=task.profile_id, task_id=task.id, script_id=script.id, script_version_id=version.id, resource_type="task", resource_id=task.id)
        provider = db.get(AIProvider, item.provider_id)
        return _proposal_dict(item, provider.name if provider else "", detail=True) | {"tasks": [task_serializer(task) for task in tasks]}

    @app.post("/api/ai/task-proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_proposal(proposal_id, user, db)
        if item.status != "DRAFT":
            raise HTTPException(status_code=409, detail="Only a draft proposal can be rejected")
        item.status = "REJECTED"; item.completed_at = now(); db.commit()
        audit(db, request, action="AI_TASK_REJECTED", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, resource_type="ai_task_proposal", resource_id=item.id)
        provider = db.get(AIProvider, item.provider_id)
        return _proposal_dict(item, provider.name if provider else "", detail=True)
