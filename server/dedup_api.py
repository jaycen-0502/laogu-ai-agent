# -*- coding: utf-8 -*-
"""
PostgreSQL / SQLite Lease Protocol v2 for Cross-Device Multi-Studio Deduplication.
Implements atomic conditional UPSERT check-and-claim, lease ownership verification,
and fail-open integration contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .models import Agent, AgentToken, StudioVisitedTarget, Workspace

logger = logging.getLogger("laogu.server.dedup")
CLAIM_TTL_MINUTES = 15
CONFIRM_TTL_DAYS = 30


def normalize_handle(value: str) -> str:
    """Accept an ASCII target name, optionally prefixed by exactly one @."""
    value = value.strip()
    if value.startswith("@"):
        value = value[1:]
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value, flags=re.ASCII):
        raise ValueError("handle must contain 1-15 ASCII letters, digits or _")
    return value.lower()


class ClaimRequest(BaseModel):
    studio_token: str | None = Field(default=None, max_length=512)
    handle: str = Field(min_length=1, max_length=64)
    lease_id: UUID
    device_name: str | None = Field(default="", max_length=120)
    account_tag: str | None = Field(default="", max_length=120)
    action: Literal["follow", "like"] = "follow"

    @field_validator("handle")
    @classmethod
    def validate_handle(cls, value: str) -> str:
        return normalize_handle(value)


class ConfirmRequest(ClaimRequest):
    pass


@dataclass(frozen=True)
class Principal:
    workspace: Any
    owner_key: str


def _current_time(db: Session) -> datetime:
    bind = getattr(db, "bind", None)
    if bind and getattr(bind.dialect, "name", "") == "postgresql":
        return db.scalar(select(func.clock_timestamp()))
    return datetime.now(timezone.utc)


def _resolve_workspace(
    db: Session,
    studio_token_param: str | None = None,
    header_studio_token: str | None = None,
    auth_header: str | None = None,
) -> Principal:
    tokens = {
        value.strip()
        for value in (studio_token_param, header_studio_token)
        if value and value.strip()
    }
    if len(tokens) > 1:
        raise HTTPException(400, "Conflicting studio credentials")

    token = next(iter(tokens), "")
    workspace = None
    if token:
        # Strict token matching: workspace.id or name are not credentials.
        workspace = db.scalar(select(Workspace).where(
            Workspace.studio_token == token,
            Workspace.status == "ACTIVE",
        ))
        if workspace is None:
            raise HTTPException(401, "Invalid studio credential")

    if auth_header is not None:
        scheme, _, raw = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not raw.strip():
            raise HTTPException(401, "Invalid agent credential")
        from .auth import token_hash

        credential = db.scalar(select(AgentToken).where(
            AgentToken.token_hash == token_hash(raw.strip()),
            AgentToken.status == "ACTIVE",
        ))
        if credential is None:
            raise HTTPException(401, "Invalid agent credential")
        expires_at = getattr(credential, "expires_at", None)
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now_dt = _current_time(db)
            if expires_at <= now_dt:
                raise HTTPException(401, "Expired agent credential")
        agent = db.scalar(select(Agent).where(
            Agent.id == credential.agent_id, Agent.status != "DELETED",
        ))
        if agent is None:
            raise HTTPException(401, "Inactive agent")
        if workspace is not None and workspace.id != agent.workspace_id:
            raise HTTPException(403, "Agent belongs to another workspace")
        if workspace is None:
            workspace = db.scalar(select(Workspace).where(
                Workspace.id == agent.workspace_id,
                Workspace.status == "ACTIVE",
            ))
        if workspace is None:
            raise HTTPException(401, "Inactive workspace")
        return Principal(workspace, f"agent:{agent.id}")

    if workspace is None:
        raise HTTPException(401, "Missing credential")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return Principal(workspace, f"studio:{digest}")


def build_claim_statement(body: ClaimRequest, principal: Principal, dialect_name: str = "postgresql"):
    table = StudioVisitedTarget.__table__
    if dialect_name == "postgresql":
        stamp = func.clock_timestamp()
        proposal = pg_insert(table).values(
            workspace_id=principal.workspace.id,
            target_handle=body.handle,
            operator_device=(body.device_name or "").strip(),
            account_tag=(body.account_tag or "").strip(),
            action=body.action,
            status="CLAIMED",
            lease_id=body.lease_id,
            owner_key=principal.owner_key,
            claimed_at=stamp,
            confirmed_at=None,
            expires_at=stamp + timedelta(minutes=CLAIM_TTL_MINUTES),
        )
        return proposal.on_conflict_do_update(
            constraint="uq_workspace_target_handle",
            set_={
                "operator_device": proposal.excluded.operator_device,
                "account_tag": proposal.excluded.account_tag,
                "action": proposal.excluded.action,
                "status": "CLAIMED",
                "lease_id": proposal.excluded.lease_id,
                "owner_key": proposal.excluded.owner_key,
                "claimed_at": func.clock_timestamp(),
                "confirmed_at": None,
                "expires_at": func.clock_timestamp() + timedelta(minutes=CLAIM_TTL_MINUTES),
            },
            where=table.c.expires_at <= func.clock_timestamp(),
        ).returning(*table.c)
    else:
        stamp = datetime.now(timezone.utc)
        proposal = sqlite_insert(table).values(
            workspace_id=principal.workspace.id,
            target_handle=body.handle,
            operator_device=(body.device_name or "").strip(),
            account_tag=(body.account_tag or "").strip(),
            action=body.action,
            status="CLAIMED",
            lease_id=body.lease_id,
            owner_key=principal.owner_key,
            claimed_at=stamp,
            confirmed_at=None,
            expires_at=stamp + timedelta(minutes=CLAIM_TTL_MINUTES),
        )
        return proposal.on_conflict_do_update(
            index_elements=["workspace_id", "target_handle"],
            set_={
                "operator_device": proposal.excluded.operator_device,
                "account_tag": proposal.excluded.account_tag,
                "action": proposal.excluded.action,
                "status": "CLAIMED",
                "lease_id": proposal.excluded.lease_id,
                "owner_key": proposal.excluded.owner_key,
                "claimed_at": stamp,
                "confirmed_at": None,
                "expires_at": stamp + timedelta(minutes=CLAIM_TTL_MINUTES),
            },
            where=table.c.expires_at <= stamp,
        ).returning(*table.c)


def _key_query(body: ClaimRequest, workspace_id: str):
    table = StudioVisitedTarget.__table__
    return select(table).where(
        table.c.workspace_id == workspace_id,
        table.c.target_handle == body.handle,
    )


def claim_in_transaction(
    db: Session, body: ClaimRequest, principal: Principal,
) -> dict[str, Any]:
    bind = getattr(db, "bind", None)
    dialect_name = getattr(bind.dialect, "name", "") if bind else "postgresql"
    stmt = build_claim_statement(body, principal, dialect_name=dialect_name)
    row = db.execute(stmt).mappings().first()
    acquired = row is not None
    if row is None:
        # READ COMMITTED: fresh snapshot. The conflict path retains lock until commit.
        row = db.execute(
            _key_query(body, principal.workspace.id)
        ).mappings().first()
    if row is None:
        raise HTTPException(503, "Claim state unavailable")

    row_lease_id = row["lease_id"]
    if isinstance(row_lease_id, str):
        try:
            row_lease_id = UUID(row_lease_id)
        except (ValueError, TypeError):
            pass

    same_attempt = (
        row["status"] == "CLAIMED"
        and row_lease_id == body.lease_id
        and row["owner_key"] == principal.owner_key
        and row["action"] == body.action
    )
    current = _current_time(db)
    row_expires = row["expires_at"]
    if row_expires is not None and getattr(row_expires, "tzinfo", None) is None:
        row_expires = row_expires.replace(tzinfo=timezone.utc)
    if current is not None and getattr(current, "tzinfo", None) is None:
        current = current.replace(tzinfo=timezone.utc)

    allowed = (acquired or same_attempt) and row_expires > current
    result = {
        "allowed": allowed,
        "reason": "" if allowed else "ALREADY_CLAIMED",
        "status": "RE_CLAIMED" if allowed and not acquired else row["status"],
        "workspace_id": principal.workspace.id,
        "workspace_name": principal.workspace.name,
        "expires_at": row_expires.isoformat(),
    }
    if allowed:
        result["lease_id"] = str(body.lease_id)
    else:
        claimed_at_val = row["claimed_at"]
        if claimed_at_val and getattr(claimed_at_val, "tzinfo", None) is None:
            claimed_at_val = claimed_at_val.replace(tzinfo=timezone.utc)
        result.update(
            claimed_by_device=row["operator_device"] or "another worker",
            claimed_by_account=row["account_tag"] or "",
            claimed_at=claimed_at_val.isoformat() if claimed_at_val else "",
        )
    return result


def confirm_in_transaction(
    db: Session, body: ConfirmRequest, principal: Principal,
) -> dict[str, Any]:
    table = StudioVisitedTarget.__table__
    bind = getattr(db, "bind", None)
    dialect_name = getattr(bind.dialect, "name", "") if bind else "postgresql"
    query = _key_query(body, principal.workspace.id)
    if dialect_name == "postgresql":
        query = query.with_for_update()
    row = db.execute(query).mappings().first()

    row_lease_id = row["lease_id"] if row is not None else None
    if isinstance(row_lease_id, str):
        try:
            row_lease_id = UUID(row_lease_id)
        except (ValueError, TypeError):
            pass

    if row is None or (
        row_lease_id != body.lease_id
        or row["owner_key"] != principal.owner_key
        or row["action"] != body.action
    ):
        raise HTTPException(409, "Lease missing or superseded")

    stamp = _current_time(db)
    row_expires = row["expires_at"]
    if row_expires is not None and getattr(row_expires, "tzinfo", None) is None:
        row_expires = row_expires.replace(tzinfo=timezone.utc)
    if stamp is not None and getattr(stamp, "tzinfo", None) is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    if row["status"] == "CONFIRMED":
        expires_at = row_expires  # Idempotent retry: never extend retention.
    elif row["status"] == "CLAIMED" and row_expires > stamp:
        expires_at = stamp + timedelta(days=CONFIRM_TTL_DAYS)
        db.execute(table.update().where(table.c.id == row["id"]).values(
            status="CONFIRMED", confirmed_at=stamp, expires_at=expires_at,
        ))
    else:
        raise HTTPException(409, "Lease expired")
    return {
        "ok": True,
        "status": "CONFIRMED",
        "workspace_id": principal.workspace.id,
        "target_handle": body.handle,
        "lease_id": str(body.lease_id),
        "expires_at": expires_at.isoformat(),
    }


def _db_error(db: Session, exc: DBAPIError) -> None:
    db.rollback()
    code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    logger.warning("Dedup DB failure: sqlstate=%s", code)
    raise HTTPException(503, "Dedup temporarily unavailable") from exc


def _start_transaction_budget(db: Session) -> None:
    bind = getattr(db, "bind", None)
    if bind and getattr(bind.dialect, "name", "") == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '200ms'"))
        db.execute(text("SET LOCAL statement_timeout = '500ms'"))


def register_dedup_routes(app: FastAPI, *, get_db) -> None:
    router = APIRouter(prefix="/api/dedup", tags=["Cross-Device Deduplication"])

    def execute_mutation(db, body, studio_header, authorization, operation):
        try:
            _start_transaction_budget(db)
            principal = _resolve_workspace(
                db, body.studio_token, studio_header, authorization,
            )
            result = operation(db, body, principal)
            db.commit()
            return result
        except DBAPIError as exc:
            _db_error(db, exc)
        except BaseException:
            db.rollback()
            raise

    @router.post("/claim")
    def claim_target(
        body: ClaimRequest,
        x_studio_token: Annotated[str | None, Header(alias="X-Studio-Token")] = None,
        authorization: Annotated[str | None, Header()] = None,
        db: Session = Depends(get_db),
    ):
        return execute_mutation(
            db, body, x_studio_token, authorization, claim_in_transaction,
        )

    @router.post("/confirm")
    def confirm_target(
        body: ConfirmRequest,
        x_studio_token: Annotated[str | None, Header(alias="X-Studio-Token")] = None,
        authorization: Annotated[str | None, Header()] = None,
        db: Session = Depends(get_db),
    ):
        return execute_mutation(
            db, body, x_studio_token, authorization, confirm_in_transaction,
        )

    @router.get("/stats")
    def dedup_stats(
        studio_token: str | None = Query(None, max_length=512),
        x_studio_token: Annotated[str | None, Header(alias="X-Studio-Token")] = None,
        authorization: Annotated[str | None, Header()] = None,
        db: Session = Depends(get_db),
    ):
        try:
            _start_transaction_budget(db)
            principal = _resolve_workspace(
                db, studio_token, x_studio_token, authorization,
            )
            table = StudioVisitedTarget.__table__
            counts = db.execute(select(
                func.count().filter(table.c.status == "CONFIRMED"),
                func.count().filter(table.c.status == "CLAIMED"),
            ).where(
                table.c.workspace_id == principal.workspace.id,
                table.c.expires_at > _current_time(db),
            )).one()
            result = {
                "workspace_id": principal.workspace.id,
                "workspace_name": principal.workspace.name,
                "active_confirmed_count": counts[0],
                "active_claimed_count": counts[1],
                "retention_days": CONFIRM_TTL_DAYS,
                "claim_ttl_minutes": CLAIM_TTL_MINUTES,
            }
            db.commit()
            return result
        except DBAPIError as exc:
            _db_error(db, exc)
        except BaseException:
            db.rollback()
            raise

    app.include_router(router)


def prune_expired_batch(db: Session, batch_size: int = 1000) -> int:
    """Dedicated maintenance transaction, outside the request path."""
    size = max(1, min(batch_size, 5000))
    bind = getattr(db, "bind", None)
    dialect_name = getattr(bind.dialect, "name", "") if bind else "postgresql"
    try:
        _start_transaction_budget(db)
        if dialect_name == "postgresql":
            result = db.execute(text("""
                WITH victims AS (
                    SELECT id
                    FROM studio_visited_targets
                    WHERE expires_at < statement_timestamp() - INTERVAL '1 day'
                    ORDER BY expires_at, id
                    LIMIT :batch_size
                    FOR UPDATE SKIP LOCKED
                )
                DELETE FROM studio_visited_targets AS target
                USING victims
                WHERE target.id = victims.id
                RETURNING target.id
            """), {"batch_size": size})
            count = len(result.fetchall())
        else:
            table = StudioVisitedTarget.__table__
            thresh = datetime.now(timezone.utc) - timedelta(days=1)
            del_stmt = table.delete().where(table.c.expires_at < thresh)
            result = db.execute(del_stmt)
            count = result.rowcount or 0
        db.commit()
        return count
    except BaseException:
        db.rollback()
        raise
