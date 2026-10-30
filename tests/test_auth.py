from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.auth import hash_password, issue_session_token, verify_password
from backend.app.db import Base, Repository


@pytest.mark.asyncio
async def test_user_password_profile_and_session_repository():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        repo = Repository(session)
        password_hash, salt = hash_password("secure-pass-123")
        user = await repo.create_user(
            user_id="u1",
            username="fox_friend",
            password_hash=password_hash,
            password_salt=salt,
            style_preference="landscape",
        )
        assert user.username == "fox_friend"
        assert verify_password("secure-pass-123", user.password_hash, user.password_salt)
        assert not verify_password("wrong-pass", user.password_hash, user.password_salt)

        updated = await repo.update_user_style("u1", "culture")
        assert updated and updated.style_preference == "culture"

        token, token_hash, expires_at = issue_session_token()
        assert token and len(token_hash) == 64
        await repo.create_auth_session(token_hash=token_hash, user_id=user.id, expires_at=expires_at)
        stored = await repo.get_auth_session(token_hash)
        assert stored and stored.user_id == "u1"
        stored_expires = stored.expires_at
        if stored_expires.tzinfo is None:
            stored_expires = stored_expires.replace(tzinfo=timezone.utc)
        assert stored_expires > datetime.now(timezone.utc) - timedelta(seconds=1)
        await repo.delete_auth_session(token_hash)
        assert await repo.get_auth_session(token_hash) is None
    await engine.dispose()
