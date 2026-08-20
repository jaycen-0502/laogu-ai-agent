from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def uid() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now().astimezone()


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="MEMBER")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(120))
    machine_name: Mapped[str] = mapped_column(String(200))
    client_version: Mapped[str] = mapped_column(String(50))
    token_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="OFFLINE")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_count: Mapped[int] = mapped_column(Integer, default=0)
    running_task_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("workspace_id", "machine_name", name="uq_agent_machine_workspace"),)


class AgentToken(Base):
    __tablename__ = "agent_tokens"
    token_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(100), index=True)
    instance_id: Mapped[str] = mapped_column(String(100), default="")
    x_username: Mapped[str] = mapped_column(String(30), default="")
    x_account_id: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    __table_args__ = (UniqueConstraint("agent_id", "profile_id", name="uq_agent_profile"),)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(100), index=True)
    instance_id: Mapped[str] = mapped_column(String(100), default="")
    x_username: Mapped[str] = mapped_column(String(30), default="")
    x_account_id: Mapped[str] = mapped_column(String(40), default="")
    login_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    browser_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    account_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mapping_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("agent_id", "profile_id", name="uq_account_agent_profile"),)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(100), index=True)
    x_account_id: Mapped[str] = mapped_column(String(40), default="")
    script_id: Mapped[str | None] = mapped_column(ForeignKey("scripts.id"), nullable=True, index=True)
    script_version_id: Mapped[str | None] = mapped_column(ForeignKey("script_versions.id"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(50))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration: Mapped[float] = mapped_column(Float, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Command(Base):
    __tablename__ = "commands"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    profile_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    command_type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(500), default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("agent_id", "idempotency_key", name="uq_command_idempotency_agent"),)


class CredentialCapability(Base):
    __tablename__ = "credential_capabilities"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(100), index=True)
    command_id: Mapped[str] = mapped_column(ForeignKey("commands.id"), unique=True, index=True)
    probe_version: Mapped[str] = mapped_column(String(20), default="1")
    browser_reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    cookie_read_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    cookie_write_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_snapshot_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str] = mapped_column(String(80), default="NOT_ADVERTISED")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    __table_args__ = (UniqueConstraint("agent_id", "profile_id", name="uq_credential_capability_agent_profile"),)


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(100), index=True)
    x_account_id: Mapped[str] = mapped_column(String(40), default="")
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    script_id: Mapped[str | None] = mapped_column(ForeignKey("scripts.id"), nullable=True, index=True)
    script_version_id: Mapped[str | None] = mapped_column(ForeignKey("script_versions.id"), nullable=True, index=True)
    activity_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    duration: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(String(200), default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    logs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    audit_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    script_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    script_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(50), default="")
    resource_id: Mapped[str] = mapped_column(String(100), default="")
    result: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    ip: Mapped[str] = mapped_column(String(80), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    message: Mapped[str] = mapped_column(String(500), default="")


class Script(Base):
    __tablename__ = "scripts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    language: Mapped[str] = mapped_column(String(20), default="javascript")
    status: Mapped[str] = mapped_column(String(20), default="DISABLED", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_script_name_workspace"),)


class ScriptVersion(Base):
    __tablename__ = "script_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)
    params_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("script_id", "version", name="uq_script_version"),)


class AIProvider(Base):
    __tablename__ = "ai_providers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    provider_type: Mapped[str] = mapped_column(String(40), default="OPENAI_COMPATIBLE")
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    api_key_last4: Mapped[str] = mapped_column(String(4), default="")
    default_model: Mapped[str] = mapped_column(String(160), default="")
    available_models: Mapped[list] = mapped_column("models", JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="DISABLED", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_test_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_ai_provider_name_workspace"),
        Index(
            "uq_ai_provider_default_workspace",
            "workspace_id",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default"),
        ),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新聊天")
    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"), index=True)
    model: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS", index=True)
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AIImage(Base):
    __tablename__ = "ai_images"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"), index=True)
    model: Mapped[str] = mapped_column(String(160), default="gpt-image-2")
    prompt: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str] = mapped_column(String(10))
    size: Mapped[str] = mapped_column(String(30))
    quality: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    mime_type: Mapped[str] = mapped_column(String(50), default="")
    file_name: Mapped[str] = mapped_column(String(100), default="")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    image_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    analysis_type: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(160), default="")
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    input_text: Mapped[str] = mapped_column(Text, default="")
    source_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIWritingRecord(Base):
    __tablename__ = "ai_writing_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    record_type: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(160), default="")
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    source_text: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str] = mapped_column(Text, default="")
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AITaskProposal(Base):
    __tablename__ = "ai_task_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"), index=True)
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    request_text: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    task_ids: Mapped[list] = mapped_column(JSON, default=list)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class License(Base):
    """Server-side metadata for a signed offline activation code.

    The complete activation code is intentionally never persisted.  The
    browser remains the authority for the local encrypted activation state;
    this table only lets the server register, check and revoke a license.
    """

    __tablename__ = "licenses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    license_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    customer: Mapped[str] = mapped_column(String(200), default="")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    features: Mapped[list] = mapped_column(JSON, default=list)
    activation_code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    offline_grace_days: Mapped[int] = mapped_column(Integer, default=7)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LicenseDevice(Base):
    __tablename__ = "license_devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    install_public_key_hash: Mapped[str] = mapped_column(String(64))
    app_version: Mapped[str] = mapped_column(String(80), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    last_ip: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    __table_args__ = (UniqueConstraint("license_id", "device_id", name="uq_license_device"),)


class LicenseCheck(Base):
    __tablename__ = "license_checks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    license_id: Mapped[str | None] = mapped_column(ForeignKey("licenses.id", ondelete="SET NULL"), nullable=True, index=True)
    external_license_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    device_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    app_version: Mapped[str] = mapped_column(String(80), default="")
    result: Mapped[str] = mapped_column(String(30), index=True)
    reason: Mapped[str] = mapped_column(String(120), default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    ip: Mapped[str] = mapped_column(String(80), default="")


class LicenseRevocation(Base):
    __tablename__ = "license_revocations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    revoked_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
