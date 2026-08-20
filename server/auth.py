from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import bcrypt
import jwt

from .config import ServerSettings


DUMMY_PASSWORD_HASH = b"$2b$12$C6UzMDM.H6dfI/f/IKcEe.ogH5dF1xHhV8g9ZQxR3fQfY7lXcD9QK"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def verify_login_password(password: str, hashed: str | None) -> bool:
    candidate = hashed.encode() if hashed else DUMMY_PASSWORD_HASH
    try:
        return bcrypt.checkpw(password.encode(), candidate)
    except ValueError:
        return False


def password_auth_version(hashed: str) -> str:
    return hashlib.sha256(hashed.encode()).hexdigest()[:16]


def create_jwt(user, settings: ServerSettings) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user.id, "role": user.role, "workspace_id": user.workspace_id, "auth_version": password_auth_version(user.password_hash), "iat": now, "exp": now + timedelta(minutes=settings.jwt_expire_minutes)},
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_jwt(token: str, settings: ServerSettings) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def new_agent_token() -> str:
    return "lag_" + secrets.token_urlsafe(32)


def new_invitation_token() -> str:
    return "lgi_" + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
