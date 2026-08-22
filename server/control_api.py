from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AIAnalysis,
    AIImage,
    AIProvider,
    AIUsage,
    AIWritingRecord,
    Account,
    Activity,
    Agent,
    AuditLog,
    AutomationMetric,
    ChatSession,
    Profile,
    Script,
    ScriptVersion,
    Task,
    User,
    Workspace,
)
from .security import audit_dict


def _dt(value):
    return value.isoformat() if value else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_control_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    settings,
    agent_serializer: Callable,
    profile_serializer: Callable,
    account_serializer: Callable,
    task_serializer: Callable,
    activity_serializer: Callable,
) -> None:
    metric_fields = ("processed_count", "likes", "follows", "comments", "scanned_posts")

    def empty_metrics() -> dict[str, int]:
        return {"automation_runs": 0, **{key: 0 for key in metric_fields}}

    def metric_dict(item: AutomationMetric) -> dict:
        return {
            "run_id": item.run_id,
            "profile_id": item.profile_id,
            "x_account_id": item.x_account_id,
            "account_tag": item.account_tag,
            "metric_date": item.metric_date.isoformat(),
            "started_at": _dt(item.started_at),
            "finished_at": _dt(item.finished_at),
            "status": item.status,
            **{key: int(getattr(item, key) or 0) for key in metric_fields},
            "own_followers": item.own_followers,
            "own_following": item.own_following,
        }

    def scoped(db: Session, model, user: User):
        query = select(model)
        if user.role != "ADMIN":
            workspace_column = getattr(model, "workspace_id", None)
            if workspace_column is None:
                workspace_column = model.id
            query = query.where(workspace_column == user.workspace_id)
        return list(db.scalars(query))

    def visible_profile(profile_record_id: str, user: User, db: Session) -> Profile:
        profile = db.get(Profile, profile_record_id)
        if not profile or (user.role != "ADMIN" and profile.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="Profile not found")
        return profile

    def task_details(db: Session, tasks: list[Task]) -> list[dict]:
        output = []
        for task in tasks:
            payload = task_serializer(task)
            script = db.get(Script, task.script_id) if task.script_id else None
            version = db.get(ScriptVersion, task.script_version_id) if task.script_version_id else None
            payload |= {
                "script_name": script.name if script else "",
                "script_version": version.version if version else None,
            }
            output.append(payload)
        return output

    @app.get("/api/control/overview")
    def control_overview(
        recent_limit: int = Query(20, ge=5, le=100),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        workspaces = scoped(db, Workspace, user)
        agents = scoped(db, Agent, user)
        profiles = scoped(db, Profile, user)
        accounts = scoped(db, Account, user)
        tasks = scoped(db, Task, user)
        activities = scoped(db, Activity, user)
        scripts = scoped(db, Script, user)
        providers = scoped(db, AIProvider, user)
        usages = scoped(db, AIUsage, user)
        analyses = scoped(db, AIAnalysis, user)
        writings = scoped(db, AIWritingRecord, user)
        images = scoped(db, AIImage, user)
        chats = scoped(db, ChatSession, user)
        metrics = scoped(db, AutomationMetric, user)
        today = datetime.now().astimezone().date()
        today_metrics = [item for item in metrics if item.metric_date == today]
        metrics_by_profile: dict[tuple[str, str], dict[str, int]] = {}
        for item in today_metrics:
            aggregate = metrics_by_profile.setdefault((item.agent_id, item.profile_id), empty_metrics())
            aggregate["automation_runs"] += 1
            for field in metric_fields:
                aggregate[field] += int(getattr(item, field) or 0)

        agent_payloads = []
        for agent in sorted(agents, key=lambda item: item.created_at or datetime.min, reverse=True):
            payload = agent_serializer(agent, settings)
            agent_tasks = [task for task in tasks if task.agent_id == agent.id]
            payload["profile_total"] = sum(1 for profile in profiles if profile.agent_id == agent.id)
            payload["pending_tasks"] = sum(1 for task in agent_tasks if task.status in {"PENDING", "DISPATCHED"})
            payload["running_tasks"] = sum(1 for task in agent_tasks if task.status == "RUNNING")
            agent_payloads.append(payload)

        accounts_by_key = {(account.agent_id, account.profile_id): account for account in accounts}
        profile_payloads = []
        for profile in sorted(profiles, key=lambda item: item.profile_id):
            account = accounts_by_key.get((profile.agent_id, profile.profile_id))
            payload = profile_serializer(profile, account)
            profile_tasks = sorted(
                (task for task in tasks if task.profile_id == profile.profile_id and task.agent_id == profile.agent_id),
                key=lambda item: item.created_at or datetime.min,
                reverse=True,
            )
            current = next((task for task in profile_tasks if task.status in {"PENDING", "DISPATCHED", "RUNNING"}), None)
            agent = db.get(Agent, profile.agent_id)
            payload |= {
                "agent_name": agent.agent_name if agent else "",
                "profile_record_id": profile.id,
                "current_task": task_serializer(current) if current else None,
                "task_count": len(profile_tasks),
                "today_metrics": metrics_by_profile.get((profile.agent_id, profile.profile_id), empty_metrics()),
            }
            profile_payloads.append(payload)

        status_counts = Counter(task.status for task in tasks)
        agent_status_counts = Counter(agent_serializer(agent, settings)["status"] for agent in agents)
        account_status_counts = Counter(account.login_status for account in accounts)
        script_status_counts = Counter(script.status for script in scripts)
        enabled_providers = sum(1 for provider in providers if provider.status == "ENABLED")

        recent_tasks = sorted(tasks, key=lambda item: item.created_at or datetime.min, reverse=True)[:recent_limit]
        recent_activities = sorted(activities, key=lambda item: item.timestamp or datetime.min, reverse=True)[:recent_limit]
        recent_audits = list(db.scalars(
            (select(AuditLog)
             .where(AuditLog.workspace_id == user.workspace_id if user.role != "ADMIN" else True)
             .order_by(AuditLog.timestamp.desc())
             .limit(recent_limit))
        ))

        return {
            "generated_at": _now_iso(),
            "scope": "global" if user.role == "ADMIN" else "workspace",
            "summary": {
                "workspace_count": len(workspaces),
                "agent_count": len(agents),
                "online_agents": agent_status_counts["ONLINE"],
                "offline_agents": agent_status_counts["OFFLINE"],
                "profile_count": len(profiles),
                "account_count": len(accounts),
                "logged_in_accounts": account_status_counts["LOGGED_IN"],
                "task_count": len(tasks),
                "pending_tasks": status_counts["PENDING"] + status_counts["DISPATCHED"],
                "running_tasks": status_counts["RUNNING"],
                "success_tasks": status_counts["SUCCESS"],
                "failed_tasks": status_counts["FAILED"] + status_counts["TIMEOUT"],
                "cancelled_tasks": status_counts["CANCELLED"],
                "script_count": len(scripts),
                "enabled_scripts": script_status_counts["ENABLED"],
                "enabled_providers": enabled_providers,
                "ai_request_count": len(usages),
                "ai_total_tokens": sum(item.total_tokens or 0 for item in usages),
                "analysis_count": len(analyses),
                "writing_count": len(writings),
                "image_count": len(images),
                "chat_session_count": len(chats),
                "today_automation_runs": len(today_metrics),
                **{
                    f"today_{field}": sum(int(getattr(item, field) or 0) for item in today_metrics)
                    for field in metric_fields
                },
            },
            "agents": agent_payloads,
            "profiles": profile_payloads,
            "recent_tasks": task_details(db, recent_tasks),
            "recent_activities": [activity_serializer(item) for item in recent_activities],
            "recent_audits": [audit_dict(item) for item in recent_audits],
        }

    @app.get("/api/control/profiles/{profile_record_id}")
    def control_profile_detail(
        profile_record_id: str,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        profile = visible_profile(profile_record_id, user, db)
        account = db.scalar(select(Account).where(Account.agent_id == profile.agent_id, Account.profile_id == profile.profile_id))
        agent = db.get(Agent, profile.agent_id)
        tasks = list(db.scalars(
            select(Task)
            .where(Task.agent_id == profile.agent_id, Task.profile_id == profile.profile_id)
            .order_by(Task.created_at.desc())
            .limit(100)
        ))
        activities = list(db.scalars(
            select(Activity)
            .where(Activity.agent_id == profile.agent_id, Activity.profile_id == profile.profile_id)
            .order_by(Activity.timestamp.desc())
            .limit(100)
        ))
        automation_metrics = list(db.scalars(
            select(AutomationMetric)
            .where(
                AutomationMetric.agent_id == profile.agent_id,
                AutomationMetric.profile_id == profile.profile_id,
            )
            .order_by(AutomationMetric.finished_at.desc())
            .limit(100)
        ))
        today = datetime.now().astimezone().date()
        profile_today = empty_metrics()
        for item in automation_metrics:
            if item.metric_date != today:
                continue
            profile_today["automation_runs"] += 1
            for field in metric_fields:
                profile_today[field] += int(getattr(item, field) or 0)
        return {
            "profile": profile_serializer(profile, account) | {
                "profile_record_id": profile.id,
                "today_metrics": profile_today,
            },
            "agent": agent_serializer(agent, settings) if agent else None,
            "account": account_serializer(account) if account else None,
            "tasks": task_details(db, tasks),
            "activities": [activity_serializer(item) for item in activities],
            "today_metrics": profile_today,
            "automation_metrics": [metric_dict(item) for item in automation_metrics],
        }

    @app.get("/api/control/timeline")
    def control_timeline(
        limit: int = Query(50, ge=1, le=200),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        activities = sorted(scoped(db, Activity, user), key=lambda item: item.timestamp or datetime.min, reverse=True)[:limit]
        audit_query = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        if user.role != "ADMIN":
            audit_query = audit_query.where(AuditLog.workspace_id == user.workspace_id)
        audits = list(db.scalars(audit_query))
        events = [
            {
                "event_type": "activity",
                "timestamp": item.timestamp.isoformat() if item.timestamp else None,
                "status": item.status,
                "action": item.activity_type,
                "resource_id": item.task_id,
                "profile_id": item.profile_id,
                "summary": item.summary,
            }
            for item in activities
        ] + [
            {
                "event_type": "audit",
                "timestamp": item.timestamp.isoformat() if item.timestamp else None,
                "status": item.result,
                "action": item.action,
                "resource_id": item.resource_id,
                "profile_id": item.profile_id,
                "summary": item.message,
            }
            for item in audits
        ]
        events.sort(key=lambda item: item["timestamp"] or "", reverse=True)
        return {"items": events[:limit], "count": min(limit, len(events))}
