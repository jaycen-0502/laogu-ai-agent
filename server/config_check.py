from __future__ import annotations

import base64

from sqlalchemy.engine import make_url

from .config import ServerSettings


DEFAULT_SECRETS = {
    "development-change-me",
    "development-only-secret-change-me-32-bytes",
    "CHANGE_TO_AT_LEAST_32_RANDOM_BYTES",
    "change-me",
}


class ProductionConfigError(RuntimeError):
    pass


def check_server_config(settings: ServerSettings) -> list[str]:
    warnings = []
    common_errors = []
    if settings.jwt_expire_minutes < 1 or settings.jwt_expire_minutes > 1440:
        common_errors.append("JWT expiry must be between 1 and 1440 minutes")
    if settings.agent_token_ttl_days < 1:
        common_errors.append("Agent token TTL must be at least one day")
    if settings.max_request_bytes < 1024:
        common_errors.append("Maximum request size must be at least 1024 bytes")
    if settings.rate_limit_window_seconds < 1:
        common_errors.append("Rate limit window must be positive")
    if settings.ai_provider_timeout_seconds < 1 or settings.ai_provider_timeout_seconds > 60:
        common_errors.append("AI provider timeout must be between 1 and 60 seconds")
    if settings.rate_limit_ai_test < 1:
        common_errors.append("AI provider test rate limit must be positive")
    if settings.ai_chat_timeout_seconds < 1 or settings.ai_chat_timeout_seconds > 600:
        common_errors.append("AI chat timeout must be between 1 and 600 seconds")
    if settings.ai_chat_max_context_messages < 2 or settings.ai_chat_max_context_messages > 500:
        common_errors.append("AI chat context message limit must be between 2 and 500")
    if settings.ai_chat_max_context_tokens < 128:
        common_errors.append("AI chat context token limit must be at least 128")
    if settings.ai_chat_max_output_tokens < 1:
        common_errors.append("AI chat output token limit must be positive")
    if settings.rate_limit_ai_chat < 1:
        common_errors.append("AI chat rate limit must be positive")
    if settings.ai_image_timeout_seconds < 30 or settings.ai_image_timeout_seconds > 600:
        common_errors.append("AI image timeout must be between 30 and 600 seconds")
    if settings.ai_image_max_bytes < 1048576 or settings.ai_image_max_bytes > 52428800:
        common_errors.append("AI image maximum size must be between 1 MiB and 50 MiB")
    if settings.rate_limit_ai_image < 1:
        common_errors.append("AI image rate limit must be positive")
    if settings.rate_limit_ai_analysis < 1:
        common_errors.append("AI analysis rate limit must be positive")
    if settings.rate_limit_ai_writing < 1:
        common_errors.append("AI writing rate limit must be positive")
    if settings.rate_limit_ai_task_proposal < 1:
        common_errors.append("AI task proposal rate limit must be positive")
    if settings.rate_limit_license_issue < 1:
        common_errors.append("License issue rate limit must be positive")
    if settings.rate_limit_license_check < 1:
        common_errors.append("License check rate limit must be positive")
    if settings.license_check_retention_days < 1 or settings.license_check_retention_days > 3650:
        common_errors.append("License check retention must be between 1 and 3650 days")
    if common_errors:
        raise ProductionConfigError("; ".join(common_errors))
    if settings.environment == "production":
        errors = []
        if settings.debug:
            errors.append("DEBUG must be disabled in production")
        if not settings.jwt_secret or settings.jwt_secret in DEFAULT_SECRETS or len(settings.jwt_secret.encode()) < 32:
            errors.append("A non-default JWT secret of at least 32 bytes is required")
        if not settings.https_enabled:
            errors.append("HTTPS must be enabled in production")
        try:
            credential_key = base64.urlsafe_b64decode(settings.ai_credential_key.encode())
            if len(credential_key) != 32:
                raise ValueError
        except Exception:
            errors.append("A valid LAOGU_AI_CREDENTIAL_KEY is required in production")
        try:
            database = make_url(settings.database_url)
            if not database.drivername.startswith("postgresql"):
                errors.append("PostgreSQL is required in production")
            if not database.password:
                errors.append("Database password is required in production")
        except Exception:
            errors.append("DATABASE_URL is invalid")
        if errors:
            raise ProductionConfigError("; ".join(errors))
    else:
        if settings.jwt_secret in DEFAULT_SECRETS:
            warnings.append("Development JWT secret is in use")
    return warnings
