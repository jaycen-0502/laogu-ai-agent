from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ServerSettings:
    database_url: str
    jwt_secret: str
    jwt_expire_minutes: int
    agent_offline_seconds: int
    environment: str = "development"
    debug: bool = False
    https_enabled: bool = False
    max_request_bytes: int = 1048576
    rate_limit_window_seconds: int = 60
    rate_limit_auth: int = 10
    rate_limit_register: int = 10
    rate_limit_heartbeat: int = 120
    rate_limit_tasks: int = 60
    agent_token_ttl_days: int = 365
    ai_credential_key: str = ""
    ai_provider_timeout_seconds: int = 10
    rate_limit_ai_test: int = 10
    ai_chat_timeout_seconds: int = 120
    ai_chat_max_context_messages: int = 40
    ai_chat_max_context_tokens: int = 12000
    ai_chat_max_output_tokens: int = 2048
    rate_limit_ai_chat: int = 30
    ai_image_timeout_seconds: int = 180
    ai_image_max_bytes: int = 26214400
    ai_image_storage_path: str = ""
    rate_limit_ai_image: int = 5
    rate_limit_ai_analysis: int = 10
    rate_limit_ai_writing: int = 10
    rate_limit_ai_task_proposal: int = 10
    license_issuer_public_key: str = ""
    license_issuer_private_key_file: str = ""
    license_issuer_key_password_file: str = ""
    rate_limit_license_issue: int = 5
    rate_limit_license_check: int = 300
    license_check_retention_days: int = 30


def load_server_settings() -> ServerSettings:
    environment = os.getenv("LAOGU_SERVER_ENVIRONMENT", "development").strip().lower()
    jwt_secret = os.getenv("LAOGU_SERVER_JWT_SECRET", "").strip()
    if environment != "production" and not jwt_secret:
        jwt_secret = "development-only-secret-change-me-32-bytes"
    return ServerSettings(
        database_url=os.getenv("LAOGU_SERVER_DATABASE_URL", f"sqlite:///{(ROOT / 'server' / 'laogu-server.db').as_posix()}"),
        jwt_secret=jwt_secret,
        jwt_expire_minutes=int(os.getenv("LAOGU_SERVER_JWT_EXPIRE_MINUTES", "720")),
        agent_offline_seconds=int(os.getenv("LAOGU_AGENT_OFFLINE_SECONDS", "90")),
        environment=environment,
        debug=os.getenv("LAOGU_SERVER_DEBUG", "false").strip().lower() in {"1", "true", "yes"},
        https_enabled=os.getenv("LAOGU_SERVER_HTTPS_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        max_request_bytes=int(os.getenv("LAOGU_SERVER_MAX_REQUEST_BYTES", "1048576")),
        rate_limit_window_seconds=int(os.getenv("LAOGU_RATE_LIMIT_WINDOW_SECONDS", "60")),
        rate_limit_auth=int(os.getenv("LAOGU_RATE_LIMIT_AUTH", "10")),
        rate_limit_register=int(os.getenv("LAOGU_RATE_LIMIT_REGISTER", "10")),
        rate_limit_heartbeat=int(os.getenv("LAOGU_RATE_LIMIT_HEARTBEAT", "120")),
        rate_limit_tasks=int(os.getenv("LAOGU_RATE_LIMIT_TASKS", "60")),
        agent_token_ttl_days=int(os.getenv("LAOGU_AGENT_TOKEN_TTL_DAYS", "365")),
        ai_credential_key=os.getenv("LAOGU_AI_CREDENTIAL_KEY", "").strip(),
        ai_provider_timeout_seconds=int(os.getenv("LAOGU_AI_PROVIDER_TIMEOUT_SECONDS", "10")),
        rate_limit_ai_test=int(os.getenv("LAOGU_RATE_LIMIT_AI_TEST", "10")),
        ai_chat_timeout_seconds=int(os.getenv("LAOGU_AI_CHAT_TIMEOUT_SECONDS", "120")),
        ai_chat_max_context_messages=int(os.getenv("LAOGU_AI_CHAT_MAX_CONTEXT_MESSAGES", "40")),
        ai_chat_max_context_tokens=int(os.getenv("LAOGU_AI_CHAT_MAX_CONTEXT_TOKENS", "12000")),
        ai_chat_max_output_tokens=int(os.getenv("LAOGU_AI_CHAT_MAX_OUTPUT_TOKENS", "2048")),
        rate_limit_ai_chat=int(os.getenv("LAOGU_RATE_LIMIT_AI_CHAT", "30")),
        ai_image_timeout_seconds=int(os.getenv("LAOGU_AI_IMAGE_TIMEOUT_SECONDS", "180")),
        ai_image_max_bytes=int(os.getenv("LAOGU_AI_IMAGE_MAX_BYTES", "26214400")),
        ai_image_storage_path=os.getenv("LAOGU_AI_IMAGE_STORAGE_PATH", "").strip(),
        rate_limit_ai_image=int(os.getenv("LAOGU_RATE_LIMIT_AI_IMAGE", "5")),
        rate_limit_ai_analysis=int(os.getenv("LAOGU_RATE_LIMIT_AI_ANALYSIS", "10")),
        rate_limit_ai_writing=int(os.getenv("LAOGU_RATE_LIMIT_AI_WRITING", "10")),
        rate_limit_ai_task_proposal=int(os.getenv("LAOGU_RATE_LIMIT_AI_TASK_PROPOSAL", "10")),
        license_issuer_public_key=os.getenv("LAOGU_LICENSE_ISSUER_PUBLIC_KEY", "").strip(),
        license_issuer_private_key_file=os.getenv("LAOGU_LICENSE_ISSUER_PRIVATE_KEY_FILE", "").strip(),
        license_issuer_key_password_file=os.getenv("LAOGU_LICENSE_ISSUER_KEY_PASSWORD_FILE", "").strip(),
        rate_limit_license_issue=int(os.getenv("LAOGU_RATE_LIMIT_LICENSE_ISSUE", "5")),
        rate_limit_license_check=int(os.getenv("LAOGU_RATE_LIMIT_LICENSE_CHECK", "300")),
        license_check_retention_days=int(os.getenv("LAOGU_LICENSE_CHECK_RETENTION_DAYS", "30")),
    )
