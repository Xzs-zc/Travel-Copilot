from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import AuthSessionRecord, Repository, UserRecord, get_session
from .schemas import UserProfile

AUTH_COOKIE_NAME = "travel_session"
SESSION_DAYS = 7
PBKDF2_ITERATIONS = 310_000


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, PBKDF2_ITERATIONS)
    return base64.b64encode(digest).decode("ascii"), base64.b64encode(actual_salt).decode("ascii")


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    salt = base64.b64decode(password_salt.encode("ascii"))
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, password_hash)


def issue_session_token() -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    return token, token_hash, expires_at


def session_cookie_kwargs(expires_at: datetime) -> dict[str, object]:
    settings = get_settings()
    secure = settings.cookie_secure if settings.cookie_secure is not None else settings.app_env == "production"
    return {
        "key": AUTH_COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
        "max_age": SESSION_DAYS * 24 * 60 * 60,
        "expires": expires_at,
    }


def user_profile(user: UserRecord) -> UserProfile:
    style = user.style_preference if user.style_preference in {"landscape", "culture", "balanced"} else "balanced"
    return UserProfile(id=user.id, username=user.username, style_preference=style)


async def optional_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> UserRecord | None:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    auth_session: AuthSessionRecord | None = await Repository(session).get_auth_session(token_hash)
    if not auth_session:
        return None
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        await Repository(session).delete_auth_session(token_hash)
        return None
    return await Repository(session).get_user_by_id(auth_session.user_id)


async def current_user(user: UserRecord | None = Depends(optional_user)) -> UserRecord:
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
