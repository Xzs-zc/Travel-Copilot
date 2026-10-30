from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    password_salt: Mapped[str] = mapped_column(String(64))
    style_preference: Mapped[str] = mapped_column(String(20), default="balanced")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PreferenceRecord(Base):
    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CacheRecord(Base):
    __tablename__ = "cache_records"
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    data: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanningSessionRecord(Base):
    __tablename__ = "planning_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRunRecord(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    events: Mapped[str] = mapped_column(Text, default="[]")
    result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportThreadRecord(Base):
    __tablename__ = "support_threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新的咨询")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportMessageRecord(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttractionTaskRecord(Base):
    __tablename__ = "attraction_tasks"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    poi_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    events: Mapped[str] = mapped_column(Text, default="[]")
    result: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


_settings = get_settings()
_engine_args: dict[str, Any] = {"echo": False}
if not _settings.database_url.startswith("sqlite"):
    _engine_args.update(
        pool_pre_ping=True,
        pool_size=_settings.database_pool_size,
        max_overflow=_settings.database_max_overflow,
    )

engine = create_async_engine(_settings.database_url, **_engine_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    if not _settings.database_auto_create:
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_user(self, *, user_id: str, username: str, password_hash: str, password_salt: str, style_preference: str) -> UserRecord:
        existing = await self.get_user_by_username(username)
        if existing:
            raise ValueError("用户名已存在")
        row = UserRecord(
            id=user_id,
            username=username,
            password_hash=password_hash,
            password_salt=password_salt,
            style_preference=style_preference,
        )
        self.session.add(row)
        await self.session.commit()
        return row

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        result = await self.session.execute(select(UserRecord).where(UserRecord.username == username))
        return result.scalars().first()

    async def get_user_by_id(self, user_id: str) -> UserRecord | None:
        return await self.session.get(UserRecord, user_id)

    async def update_user_style(self, user_id: str, style_preference: str) -> UserRecord | None:
        row = await self.get_user_by_id(user_id)
        if row is None:
            return None
        row.style_preference = style_preference
        row.updated_at = utcnow()
        await self.session.commit()
        return row

    async def create_auth_session(self, *, token_hash: str, user_id: str, expires_at: datetime) -> None:
        self.session.add(AuthSessionRecord(token_hash=token_hash, user_id=user_id, expires_at=expires_at))
        await self.session.commit()

    async def get_auth_session(self, token_hash: str) -> AuthSessionRecord | None:
        return await self.session.get(AuthSessionRecord, token_hash)

    async def delete_auth_session(self, token_hash: str) -> None:
        row = await self.session.get(AuthSessionRecord, token_hash)
        if row:
            await self.session.delete(row)
            await self.session.commit()

    async def get_preferences(self) -> dict[str, Any]:
        row = await self.session.get(PreferenceRecord, 1)
        if not row:
            return {}
        try:
            return json.loads(row.data)
        except json.JSONDecodeError:
            return {}

    async def save_preferences(self, data: dict[str, Any]) -> dict[str, Any]:
        row = await self.session.get(PreferenceRecord, 1)
        payload = json.dumps(data, ensure_ascii=False)
        if row:
            row.data = payload
            row.updated_at = utcnow()
        else:
            self.session.add(PreferenceRecord(id=1, data=payload))
        await self.session.commit()
        return data

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        row = await self.session.get(PlanningSessionRecord, session_id)
        payload = json.dumps(data, ensure_ascii=False, default=str)
        if row:
            row.data = payload
            row.updated_at = utcnow()
        else:
            self.session.add(PlanningSessionRecord(id=session_id, data=payload))
        await self.session.commit()

    async def get_planning_session(self, session_id: str) -> dict[str, Any] | None:
        row = await self.session.get(PlanningSessionRecord, session_id)
        if not row:
            return None
        try:
            return json.loads(row.data)
        except json.JSONDecodeError:
            return None

    async def save_agent_run(self, run_id: str, task: str, status: str, events: list[dict[str, Any]], result: dict[str, Any] | None = None) -> None:
        row = await self.session.get(AgentRunRecord, run_id)
        event_payload = json.dumps(events, ensure_ascii=False, default=str)
        result_payload = json.dumps(result or {}, ensure_ascii=False, default=str)
        if row:
            row.status = status
            row.events = event_payload
            row.result = result_payload
            row.updated_at = utcnow()
        else:
            self.session.add(AgentRunRecord(id=run_id, task=task, status=status, events=event_payload, result=result_payload))
        await self.session.commit()

    async def cache_get(self, kind: str, key: str, ttl_seconds: int) -> Any | None:
        from .cache import redis_cache_get

        cached = await redis_cache_get(kind, key)
        if cached is not None:
            return cached
        row = await self.session.get(CacheRecord, (kind, key))
        if not row:
            return None
        updated = row.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if (utcnow() - updated).total_seconds() > ttl_seconds:
            return None
        try:
            return json.loads(row.data)
        except json.JSONDecodeError:
            return None

    async def cache_set(self, kind: str, key: str, value: Any, ttl_seconds: int = 86_400) -> None:
        from .cache import redis_cache_set

        if await redis_cache_set(kind, key, value, ttl_seconds):
            return
        row = await self.session.get(CacheRecord, (kind, key))
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if row:
            row.data = payload
            row.updated_at = utcnow()
        else:
            self.session.add(CacheRecord(kind=kind, key=key, data=payload))
        await self.session.commit()

    async def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        row = await self.session.get(AgentRunRecord, run_id)
        if not row:
            return None
        try:
            events = json.loads(row.events)
        except json.JSONDecodeError:
            events = []
        try:
            result = json.loads(row.result)
        except json.JSONDecodeError:
            result = {}
        return {
            "id": row.id,
            "task": row.task,
            "status": row.status,
            "events": events,
            "result": result,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        result = await self.session.execute(select(AgentRunRecord).order_by(AgentRunRecord.updated_at.desc()).limit(limit))
        return [
            {"id": row.id, "task": row.task, "status": row.status, "updated_at": row.updated_at}
            for row in result.scalars().all()
        ]

    async def create_support_thread(self, *, thread_id: str, user_id: str, title: str) -> SupportThreadRecord:
        row = SupportThreadRecord(id=thread_id, user_id=user_id, title=title[:120])
        self.session.add(row)
        await self.session.commit()
        return row

    async def get_support_thread(self, thread_id: str, user_id: str) -> SupportThreadRecord | None:
        result = await self.session.execute(
            select(SupportThreadRecord).where(
                SupportThreadRecord.id == thread_id,
                SupportThreadRecord.user_id == user_id,
            )
        )
        return result.scalars().first()

    async def list_support_threads(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(SupportThreadRecord)
            .where(SupportThreadRecord.user_id == user_id)
            .order_by(SupportThreadRecord.updated_at.desc())
            .limit(limit)
        )
        return [
            {
                "id": row.id,
                "title": row.title,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in result.scalars().all()
        ]

    async def add_support_message(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> SupportMessageRecord:
        row = SupportMessageRecord(
            thread_id=thread_id,
            role=role,
            content=content,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False, default=str),
        )
        self.session.add(row)
        thread = await self.session.get(SupportThreadRecord, thread_id)
        if thread is not None:
            thread.updated_at = utcnow()
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def list_support_messages(
        self,
        *,
        thread_id: str,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        thread = await self.get_support_thread(thread_id, user_id)
        if thread is None:
            return []
        result = await self.session.execute(
            select(SupportMessageRecord)
            .where(SupportMessageRecord.thread_id == thread_id)
            .order_by(SupportMessageRecord.created_at.asc(), SupportMessageRecord.id.asc())
            .limit(limit)
        )
        items: list[dict[str, Any]] = []
        for row in result.scalars().all():
            try:
                metadata = json.loads(row.metadata_json)
            except json.JSONDecodeError:
                metadata = {}
            items.append(
                {
                    "id": row.id,
                    "thread_id": row.thread_id,
                    "role": row.role,
                    "content": row.content,
                    "metadata": metadata,
                    "created_at": row.created_at,
                }
            )
        return items

    async def latest_cache_value(self, *, kind: str, key_suffix: str, ttl_seconds: int) -> Any | None:
        result = await self.session.execute(
            select(CacheRecord)
            .where(CacheRecord.kind == kind, CacheRecord.key.like(f"%{key_suffix}"))
            .order_by(CacheRecord.updated_at.desc())
            .limit(1)
        )
        row = result.scalars().first()
        if row is None:
            return None
        updated = row.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if (utcnow() - updated).total_seconds() > ttl_seconds:
            return None
        try:
            return json.loads(row.data)
        except json.JSONDecodeError:
            return None
