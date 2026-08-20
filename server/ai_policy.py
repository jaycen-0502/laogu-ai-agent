from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AIProvider, User, UserAIPolicy

FEATURES = ("CHAT", "WRITING", "ANALYSIS", "TASKS", "IMAGES")
DEFAULT_MEMBER_FEATURES = {"CHAT", "WRITING", "ANALYSIS", "TASKS"}


def normalized_feature(value: str) -> str:
    feature = str(value or "").strip().upper()
    if feature not in FEATURES:
        raise HTTPException(status_code=422, detail="Invalid AI feature")
    return feature


def policy_for(db: Session, user: User, feature: str) -> UserAIPolicy | None:
    return db.scalar(select(UserAIPolicy).where(UserAIPolicy.user_id == user.id, UserAIPolicy.feature == normalized_feature(feature)))


def feature_enabled(db: Session, user: User, feature: str) -> bool:
    feature = normalized_feature(feature)
    if user.role in {"ADMIN", "OWNER"}:
        return True
    item = policy_for(db, user, feature)
    return bool(item.enabled) if item else feature in DEFAULT_MEMBER_FEATURES


def require_feature(db: Session, user: User, feature: str) -> UserAIPolicy | None:
    if not feature_enabled(db, user, feature):
        raise HTTPException(status_code=403, detail="This AI feature is not assigned to your account")
    return policy_for(db, user, feature)


def resolve_provider(
    db: Session,
    user: User,
    feature: str,
    provider_id: str | None = None,
    model: str | None = None,
    *,
    workspace_id: str | None = None,
) -> tuple[AIProvider, str]:
    feature = normalized_feature(feature)
    policy = require_feature(db, user, feature)
    target_workspace = workspace_id or user.workspace_id
    if not target_workspace:
        raise HTTPException(status_code=422, detail="User workspace is required")
    if user.role == "MEMBER":
        provider_id = policy.provider_id if policy else None
        model = policy.model if policy else None
    query = select(AIProvider).where(AIProvider.workspace_id == target_workspace, AIProvider.status == "ENABLED")
    query = query.where(AIProvider.id == provider_id) if provider_id else query.where(AIProvider.is_default.is_(True))
    provider = db.scalar(query)
    if not provider:
        raise HTTPException(status_code=422, detail="Enabled AI provider is not assigned")
    selected_model = str(model or provider.default_model or "").strip()
    if not selected_model:
        raise HTTPException(status_code=422, detail="AI model is not assigned")
    allowed = set(provider.available_models or [])
    if provider.default_model:
        allowed.add(provider.default_model)
    if allowed and selected_model not in allowed:
        raise HTTPException(status_code=422, detail="AI model is not allowed for this provider")
    return provider, selected_model


def public_permissions(db: Session, user: User) -> dict[str, bool]:
    return {feature: feature_enabled(db, user, feature) for feature in FEATURES}
