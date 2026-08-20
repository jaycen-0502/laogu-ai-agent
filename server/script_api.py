from __future__ import annotations

from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from common.script_validation import (
    ScriptValidationError,
    source_sha256,
    validate_params_schema,
    validate_script_params,
    validate_script_source,
)

from .models import Agent, Profile, Script, ScriptVersion, Task, User, Workspace, now
from .schemas import ScriptCreate, ScriptExecute, ScriptUpdate, ScriptVersionCreate
from .security import audit


def _dt(value):
    return value.isoformat() if value else None


def _script_dict(item: Script, creator: User | None = None) -> dict:
    return {
        "script_id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "description": item.description,
        "language": item.language,
        "status": item.status,
        "current_version": item.current_version,
        "created_by": item.created_by,
        "created_by_username": creator.username if creator else "",
        "created_at": _dt(item.created_at),
        "updated_at": _dt(item.updated_at),
    }


def _version_dict(item: ScriptVersion, *, include_source: bool = False) -> dict:
    payload = {
        "script_version_id": item.id,
        "script_id": item.script_id,
        "version": item.version,
        "params_schema": item.params_schema or {},
        "sha256": item.sha256,
        "created_by": item.created_by,
        "created_at": _dt(item.created_at),
    }
    if include_source:
        payload["source"] = item.source
    return payload


def register_script_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    current_agent: Callable,
    paged: Callable,
    deny: Callable,
    task_serializer: Callable,
) -> None:
    def visible_script(script_id: str, user: User, db: Session) -> Script:
        item = db.get(Script, script_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="Script not found")
        return item

    def require_editor(request: Request, user: User, db: Session, script: Script | None = None) -> None:
        workspace_id = script.workspace_id if script else user.workspace_id
        if user.role == "ADMIN" or (user.role == "OWNER" and user.workspace_id == workspace_id):
            return
        deny(request, db, action="SCRIPT_MANAGE", user=user, message="Script modification is forbidden")

    def checked_source(source: str) -> str:
        try:
            return validate_script_source(source)
        except ScriptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def checked_schema(schema: dict) -> dict:
        try:
            return validate_params_schema(schema)
        except ScriptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/scripts")
    def list_scripts(
        q: str = "",
        status: str = "",
        workspace_id: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        paged_response: bool = Query(False, alias="paged"),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(Script)
        if user.role != "ADMIN":
            query = query.where(Script.workspace_id == user.workspace_id)
        elif workspace_id.strip():
            query = query.where(Script.workspace_id == workspace_id.strip())
        if q.strip():
            value = f"%{q.strip()}%"
            query = query.where(or_(Script.name.ilike(value), Script.description.ilike(value)))
        if status.strip():
            query = query.where(Script.status == status.upper())
        query = query.order_by(Script.updated_at.desc())

        def serialize(item: Script) -> dict:
            return _script_dict(item, db.get(User, item.created_by))

        if paged_response:
            return paged(db, query, serialize, page=page, page_size=page_size)
        return [serialize(item) for item in db.scalars(query)]

    @app.post("/api/scripts")
    def create_script(body: ScriptCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        require_editor(request, user, db)
        workspace_id = body.workspace_id if user.role == "ADMIN" and body.workspace_id else user.workspace_id
        if not workspace_id or not db.get(Workspace, workspace_id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        if body.language.lower() != "javascript":
            raise HTTPException(status_code=422, detail="Only JavaScript scripts are supported")
        name = body.name.strip()
        if db.scalar(select(Script).where(Script.workspace_id == workspace_id, func.lower(Script.name) == name.lower())):
            raise HTTPException(status_code=409, detail="Script name already exists")
        source = checked_source(body.source)
        schema = checked_schema(body.params_schema)
        item = Script(
            workspace_id=workspace_id,
            name=name,
            description=body.description.strip(),
            language="javascript",
            status="DISABLED",
            current_version=1,
            created_by=user.id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(item)
        db.flush()
        version = ScriptVersion(
            script_id=item.id,
            version=1,
            source=source,
            params_schema=schema,
            sha256=source_sha256(source),
            created_by=user.id,
            created_at=now(),
        )
        db.add(version)
        db.commit()
        audit(db, request, action="SCRIPT_CREATED", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, script_id=item.id, script_version_id=version.id, resource_type="script", resource_id=item.id)
        audit(db, request, action="SCRIPT_VERSION_CREATED", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, script_id=item.id, script_version_id=version.id, resource_type="script_version", resource_id=version.id)
        return _script_dict(item, user) | {"current_version_detail": _version_dict(version, include_source=True)}

    @app.get("/api/scripts/{script_id}")
    def script_detail(script_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_script(script_id, user, db)
        version = db.scalar(select(ScriptVersion).where(ScriptVersion.script_id == item.id, ScriptVersion.version == item.current_version))
        return _script_dict(item, db.get(User, item.created_by)) | {"current_version_detail": _version_dict(version, include_source=True) if version else None}

    @app.patch("/api/scripts/{script_id}")
    def update_script(script_id: str, body: ScriptUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_script(script_id, user, db)
        require_editor(request, user, db, item)
        action = "SCRIPT_UPDATED"
        if body.name is not None:
            name = body.name.strip()
            duplicate = db.scalar(select(Script).where(Script.workspace_id == item.workspace_id, func.lower(Script.name) == name.lower(), Script.id != item.id))
            if duplicate:
                raise HTTPException(status_code=409, detail="Script name already exists")
            item.name = name
        if body.description is not None:
            item.description = body.description.strip()
        if body.status is not None:
            status = body.status.upper()
            if status not in {"ENABLED", "DISABLED"}:
                raise HTTPException(status_code=422, detail="Invalid script status")
            if item.status != status:
                action = "SCRIPT_ENABLED" if status == "ENABLED" else "SCRIPT_DISABLED"
                item.status = status
        item.updated_at = now()
        db.commit()
        audit(db, request, action=action, result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, script_id=item.id, resource_type="script", resource_id=item.id)
        return _script_dict(item, db.get(User, item.created_by))

    @app.get("/api/scripts/{script_id}/versions")
    def list_versions(script_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_script(script_id, user, db)
        versions = db.scalars(select(ScriptVersion).where(ScriptVersion.script_id == item.id).order_by(ScriptVersion.version.desc()))
        return [_version_dict(version) for version in versions]

    @app.get("/api/scripts/{script_id}/versions/{version_id}")
    def version_detail(script_id: str, version_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_script(script_id, user, db)
        version = db.get(ScriptVersion, version_id)
        if not version or version.script_id != item.id:
            raise HTTPException(status_code=404, detail="Script version not found")
        return _version_dict(version, include_source=True)

    @app.post("/api/scripts/{script_id}/versions")
    def create_version(script_id: str, body: ScriptVersionCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_script(script_id, user, db)
        require_editor(request, user, db, item)
        source = checked_source(body.source)
        schema = checked_schema(body.params_schema)
        locked = db.scalar(select(Script).where(Script.id == item.id).with_for_update()) or item
        number = locked.current_version + 1
        version = ScriptVersion(script_id=locked.id, version=number, source=source, params_schema=schema, sha256=source_sha256(source), created_by=user.id, created_at=now())
        db.add(version)
        db.flush()
        locked.current_version = number
        locked.updated_at = now()
        db.commit()
        audit(db, request, action="SCRIPT_UPDATED", result="SUCCESS", user_id=user.id, workspace_id=locked.workspace_id, script_id=locked.id, script_version_id=version.id, resource_type="script", resource_id=locked.id)
        audit(db, request, action="SCRIPT_VERSION_CREATED", result="SUCCESS", user_id=user.id, workspace_id=locked.workspace_id, script_id=locked.id, script_version_id=version.id, resource_type="script_version", resource_id=version.id)
        return _version_dict(version, include_source=True)

    @app.post("/api/scripts/{script_id}/versions/{version_id}/rollback")
    def rollback_version(script_id: str, version_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_script(script_id, user, db)
        require_editor(request, user, db, item)
        source_version = db.get(ScriptVersion, version_id)
        if not source_version or source_version.script_id != item.id:
            raise HTTPException(status_code=404, detail="Script version not found")
        locked = db.scalar(select(Script).where(Script.id == item.id).with_for_update()) or item
        number = locked.current_version + 1
        version = ScriptVersion(script_id=locked.id, version=number, source=source_version.source, params_schema=source_version.params_schema, sha256=source_version.sha256, created_by=user.id, created_at=now())
        db.add(version)
        db.flush()
        locked.current_version = number
        locked.updated_at = now()
        db.commit()
        audit(db, request, action="SCRIPT_ROLLBACK", result="SUCCESS", user_id=user.id, workspace_id=locked.workspace_id, script_id=locked.id, script_version_id=version.id, resource_type="script_version", resource_id=version.id, message=f"rollback_from={source_version.id}")
        audit(db, request, action="SCRIPT_VERSION_CREATED", result="SUCCESS", user_id=user.id, workspace_id=locked.workspace_id, script_id=locked.id, script_version_id=version.id, resource_type="script_version", resource_id=version.id)
        return _version_dict(version, include_source=True)

    @app.post("/api/scripts/{script_id}/execute")
    def execute_script(script_id: str, body: ScriptExecute, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_script(script_id, user, db)
        if item.status != "ENABLED":
            raise HTTPException(status_code=409, detail="Script is disabled")
        version = db.get(ScriptVersion, body.script_version_id) if body.script_version_id else db.scalar(select(ScriptVersion).where(ScriptVersion.script_id == item.id, ScriptVersion.version == item.current_version))
        if not version or version.script_id != item.id:
            raise HTTPException(status_code=404, detail="Script version not found")
        try:
            parameters = validate_script_params(body.params, version.params_schema)
        except ScriptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        profile_ids = list(dict.fromkeys(str(profile_id).strip() for profile_id in body.profile_ids if str(profile_id).strip()))
        if not profile_ids:
            raise HTTPException(status_code=422, detail="At least one Profile is required")
        # Validate the complete routing set before creating anything.  This keeps
        # a bad Profile near the end of a multi-Profile request from leaving
        # partially committed tasks for the Profiles that appeared before it.
        routes: list[tuple[Profile, Agent]] = []
        for profile_id in profile_ids:
            profiles = list(db.scalars(select(Profile).where(Profile.workspace_id == item.workspace_id, Profile.profile_id == profile_id)))
            if len(profiles) != 1:
                raise HTTPException(status_code=404 if not profiles else 409, detail="Profile not found" if not profiles else "Profile is ambiguous")
            profile = profiles[0]
            agent = db.get(Agent, profile.agent_id)
            if not agent or agent.workspace_id != item.workspace_id:
                deny(request, db, action="SCRIPT_EXECUTED", user=user, agent=agent, message="Workspace routing mismatch")
            routes.append((profile, agent))

        tasks = []
        for profile, agent in routes:
            task = Task(
                workspace_id=item.workspace_id,
                agent_id=profile.agent_id,
                profile_id=profile.profile_id,
                x_account_id=profile.x_account_id,
                script_id=item.id,
                script_version_id=version.id,
                task_type="script.execute",
                params={"script_id": item.id, "script_version_id": version.id, "params": parameters, "timeout": body.timeout},
                timeout=body.timeout,
            )
            db.add(task)
            db.flush()
            audit(db, request, action="SCRIPT_EXECUTED", result="SUCCESS", user_id=user.id, workspace_id=item.workspace_id, agent_id=agent.id, profile_id=profile.profile_id, task_id=task.id, script_id=item.id, script_version_id=version.id, resource_type="task", resource_id=task.id)
            tasks.append(task)
        db.commit()
        return {"items": [task_serializer(task) for task in tasks], "count": len(tasks)}

    @app.get("/api/script-runs")
    def script_runs(
        q: str = "",
        status: str = "",
        script_id: str = "",
        profile_id: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(Task).where(Task.task_type == "script.execute")
        if user.role != "ADMIN":
            query = query.where(Task.workspace_id == user.workspace_id)
        if status.strip():
            query = query.where(Task.status == status.upper())
        if script_id.strip():
            query = query.where(Task.script_id == script_id.strip())
        if profile_id.strip():
            query = query.where(Task.profile_id == profile_id.strip())
        if q.strip():
            value = f"%{q.strip()}%"
            query = query.outerjoin(Script, Script.id == Task.script_id).where(or_(Task.id.ilike(value), Task.profile_id.ilike(value), Script.name.ilike(value)))
        query = query.order_by(Task.created_at.desc())

        def serialize(task: Task) -> dict:
            script = db.get(Script, task.script_id) if task.script_id else None
            version = db.get(ScriptVersion, task.script_version_id) if task.script_version_id else None
            return task_serializer(task) | {"script_name": script.name if script else "", "script_version": version.version if version else None}

        return paged(db, query, serialize, page=page, page_size=page_size)

    @app.get("/api/agent/tasks/{task_id}/script")
    def agent_task_script(task_id: str, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        task = db.get(Task, task_id)
        if not task or task.agent_id != agent.id or task.task_type != "script.execute":
            raise HTTPException(status_code=404, detail="Script task not found")
        if task.status == "CANCELLED":
            raise HTTPException(status_code=409, detail="Task is cancelled")
        script = db.get(Script, task.script_id) if task.script_id else None
        version = db.get(ScriptVersion, task.script_version_id) if task.script_version_id else None
        if not script or not version or version.script_id != script.id or script.workspace_id != agent.workspace_id or task.workspace_id != agent.workspace_id:
            raise HTTPException(status_code=404, detail="Script version not found for agent")
        return _version_dict(version, include_source=True) | {"language": script.language, "workspace_id": script.workspace_id, "task_id": task.id}

    @app.get("/api/agent/tasks/{task_id}/status")
    def agent_task_status(task_id: str, agent: Agent = Depends(current_agent), db: Session = Depends(get_db)):
        task = db.get(Task, task_id)
        if not task or task.agent_id != agent.id:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"task_id": task.id, "status": task.status}
