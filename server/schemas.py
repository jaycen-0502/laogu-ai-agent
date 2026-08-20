from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class BootstrapRequest(BaseModel):
    workspace_name: str
    username: str
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    username: str
    password: str


class LicenseRegister(BaseModel):
    activation_code: str = Field(min_length=32, max_length=20000)
    offline_grace_days: int = Field(default=7, ge=3, le=30)
    notes: str = Field(default="", max_length=300)


class LicenseCheckRequest(BaseModel):
    activation_code: str = Field(min_length=32, max_length=20000)
    device_id: str = Field(min_length=1, max_length=128)
    install_public_key: str = Field(min_length=1, max_length=200)
    app_version: str = Field(default="", max_length=80)


class LicenseRenew(BaseModel):
    activation_code: str = Field(min_length=32, max_length=20000)
    offline_grace_days: int | None = Field(default=None, ge=3, le=30)


class LicenseRevoke(BaseModel):
    license_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=300)


class LicenseIssue(BaseModel):
    request_code: str = Field(min_length=32, max_length=20000)
    days: int = Field(default=30, ge=1, le=3650)
    customer: str = Field(default="", max_length=200)
    license_id: str = Field(default="", max_length=120)
    features: list[str] = Field(default_factory=lambda: ["browser", "playwright", "external_api"], max_length=20)
    offline_grace_days: int = Field(default=7, ge=3, le=30)


class InvitationCreate(BaseModel):
    role: Literal["OWNER", "MEMBER"] = "MEMBER"
    workspace_id: str | None = None
    expires_hours: int = Field(default=72, ge=1, le=168)


class InvitationAccept(BaseModel):
    username: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    status: str | None = None


class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: str = "MEMBER"
    workspace_id: str | None = None


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: str | None = None
    workspace_id: str | None = None
    status: str | None = None


class UserAIPolicyUpdate(BaseModel):
    feature: Literal["CHAT", "WRITING", "ANALYSIS", "TASKS", "IMAGES"]
    enabled: bool = True
    provider_id: str | None = None
    model: str | None = Field(default=None, max_length=160)


class AgentRegister(BaseModel):
    agent_name: str
    machine_name: str
    client_version: str
    workspace_id: str | None = None
    device_id: str = Field(default="", max_length=128)


class Heartbeat(BaseModel):
    agent_id: str
    device_id: str = Field(default="", max_length=128)
    client_version: str | None = Field(default=None, max_length=50)
    status: str = "ONLINE"
    profile_count: int = 0
    running_task_count: int = 0
    timestamp: datetime


class AccountSyncItem(BaseModel):
    profile_id: str
    instance_id: str = ""
    x_username: str = ""
    x_account_id: str = ""
    login_status: str = "UNKNOWN"
    browser_status: str = "UNKNOWN"
    account_status: str = "UNKNOWN"
    last_checked: datetime | None = None
    mapping_updated_at: datetime | None = None


class AccountSync(BaseModel):
    agent_id: str
    items: list[AccountSyncItem]


class TaskCreate(BaseModel):
    profile_id: str
    task_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(default=30, ge=1, le=300)


class TaskPull(BaseModel):
    agent_id: str
    limit: int = Field(default=10, ge=1, le=100)


class TaskResult(BaseModel):
    task_id: str
    agent_id: str
    profile_id: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration: float = 0
    result: dict[str, Any] | None = None
    error: str | None = None


class CommandCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=32)
    profile_id: str | None = Field(default=None, max_length=100)
    task_id: str | None = Field(default=None, max_length=32)
    command_type: Literal[
        "START_PROFILE",
        "STOP_PROFILE",
        "START_TASK",
        "STOP_TASK",
        "UPDATE_PARAMS",
        "UPDATE_KEYWORDS",
        "REFRESH_PROFILE",
        "PROBE_CREDENTIAL_CAPABILITY",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)


class CommandPull(BaseModel):
    agent_id: str
    limit: int = Field(default=10, ge=1, le=50)


class CommandAck(BaseModel):
    agent_id: str
    status: Literal["ACKNOWLEDGED", "RUNNING"] = "ACKNOWLEDGED"


class CommandResult(BaseModel):
    agent_id: str
    status: Literal["SUCCESS", "FAILED", "CANCELLED"]
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=500)


class ScriptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    language: str = "javascript"
    source: str = Field(min_length=1)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str | None = None


class ScriptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    status: str | None = None


class ScriptVersionCreate(BaseModel):
    source: str = Field(min_length=1)
    params_schema: dict[str, Any] = Field(default_factory=dict)


class ScriptExecute(BaseModel):
    profile_ids: list[str] = Field(min_length=1, max_length=100)
    script_version_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(default=30, ge=1, le=300)


class AIProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_type: str = "OPENAI_COMPATIBLE"
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(min_length=1, max_length=1000)
    default_model: str = Field(default="", max_length=160)
    status: str = "DISABLED"
    is_default: bool = False
    workspace_id: str | None = None


class AIProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_type: str | None = None
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, min_length=1, max_length=1000)
    default_model: str | None = Field(default=None, max_length=160)
    status: str | None = None
    is_default: bool | None = None


class ChatSessionCreate(BaseModel):
    title: str = Field(default="新聊天", min_length=1, max_length=120)
    provider_id: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    system_prompt: str | None = Field(default=None, max_length=20000)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class AIImageGenerate(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    provider_id: str | None = Field(default=None, max_length=32)
    resolution: Literal["1K", "2K"] = "1K"
    quality: Literal["low", "medium", "high"] = "medium"


class AIAccountAnalysisCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=32)
    provider_id: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    lookback_days: int = Field(default=30, ge=1, le=365)


class AIKeywordAnalysisCreate(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=20)
    input_text: str = Field(default="", max_length=20000)
    account_id: str | None = Field(default=None, max_length=32)
    provider_id: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    lookback_days: int = Field(default=30, ge=1, le=365)
    title: str = Field(default="", max_length=160)


class AIWritingAnalyzeCreate(BaseModel):
    source_text: str = Field(min_length=1, max_length=10000)
    context_text: str = Field(default="", max_length=10000)
    provider_id: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    account_id: str | None = Field(default=None, max_length=32)
    title: str = Field(default="", max_length=160)


class AIReplyGenerateCreate(BaseModel):
    source_text: str = Field(min_length=1, max_length=10000)
    context_text: str = Field(default="", max_length=10000)
    objective: str = Field(default="", max_length=500)
    brand_voice: str = Field(default="", max_length=2000)
    tone: Literal["PROFESSIONAL", "FRIENDLY", "CONCISE", "PERSUASIVE"] = "FRIENDLY"
    language: Literal["AUTO", "ZH", "EN"] = "AUTO"
    variant_count: int = Field(default=3, ge=1, le=5)
    max_characters: int = Field(default=280, ge=40, le=1000)
    provider_id: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    account_id: str | None = Field(default=None, max_length=32)
    title: str = Field(default="", max_length=160)


class AITaskProposalCreate(BaseModel):
    request_text: str = Field(min_length=1, max_length=10000)
    provider_id: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    timeout: int = Field(default=60, ge=1, le=300)
