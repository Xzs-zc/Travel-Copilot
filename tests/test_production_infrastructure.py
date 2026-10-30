import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.attractions.durable import DurableAttractionStore, task_snapshot
from backend.app.cache import cache_compute_lock, redis_cache_get, redis_cache_set
from backend.app.db import Base
from backend.app.rate_limit import MemoryTokenBucket, RequestGuardMiddleware
from backend.app.resilience import CircuitOpenError, ProviderGuard
from backend.app.schemas import AgentEvent


@pytest.mark.asyncio
async def test_durable_task_is_idempotent_and_persists_events(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        store = DurableAttractionStore(session)
        first = await store.get_or_create("poi-1")
        second = await store.get_or_create("poi-1")
        assert first.job_id == second.job_id

        claimed = await store.claim(first.job_id)
        assert claimed is not None
        assert claimed.attempts == 1
        await store.append_event(
            first.job_id,
            AgentEvent(event="tool_result", data={"tool": "get_attraction_detail", "ok": True}),
        )
        await store.mark_completed(first.job_id, {"name": "测试景区"})

    async with sessions() as session:
        snapshot = task_snapshot(await DurableAttractionStore(session).latest_for_poi("poi-1"))
        assert snapshot["status"] == "completed"
        assert snapshot["result"] == {"name": "测试景区"}
        assert [event["event"] for event in snapshot["events"]] == ["status", "tool_result"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_durable_task_is_requeued_with_same_job_id(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create("poi-retry")
        await store.claim(task.job_id)
        await store.mark_failed(task.job_id, "upstream unavailable")
        retried = await store.get_or_create("poi-retry")
        assert retried.job_id == task.job_id
        assert retried.status == "queued"
        assert retried.attempts == 0
        assert retried.error == ""
    await engine.dispose()


@pytest.mark.asyncio
async def test_local_cache_compute_lock_serializes_same_key():
    active = 0
    overlap = False

    async def worker():
        nonlocal active, overlap
        async with cache_compute_lock("test", "same-key") as acquired:
            assert acquired
            active += 1
            overlap = overlap or active > 1
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(worker(), worker(), worker())
    assert overlap is False


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def set(self, key, value, ex):
        self.values[key] = value
        self.ttls[key] = ex

    async def get(self, key):
        return self.values.get(key)


@pytest.mark.asyncio
async def test_redis_cache_round_trip_uses_ttl(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("backend.app.cache.get_redis", lambda: fake)
    await redis_cache_set("province_attractions", "route-key", {"items": [1, 2]}, 120)
    assert await redis_cache_get("province_attractions", "route-key") == {"items": [1, 2]}
    assert list(fake.ttls.values()) == [120]


@pytest.mark.asyncio
async def test_token_bucket_returns_retry_after():
    limiter = MemoryTokenBucket()
    first = await limiter.consume("client", capacity=2, refill_per_second=0.01)
    second = await limiter.consume("client", capacity=2, refill_per_second=0.01)
    third = await limiter.consume("client", capacity=2, refill_per_second=0.01)
    assert first.allowed and second.allowed
    assert third.allowed is False
    assert third.retry_after >= 1


@pytest.mark.asyncio
async def test_rate_limit_middleware_returns_standard_error():
    app = FastAPI()
    app.add_middleware(RequestGuardMiddleware)

    @app.get("/api/auth/probe")
    async def probe():
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [await client.get("/api/auth/probe") for _ in range(11)]
    limited = responses[-1]
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.json()["error"]["request_id"] == limited.headers["x-request-id"]


@pytest.mark.asyncio
async def test_provider_guard_retries_and_opens_circuit(monkeypatch):
    guard = ProviderGuard()
    monkeypatch.setattr(guard.settings, "provider_failure_threshold", 2)
    monkeypatch.setattr(guard.settings, "provider_recovery_seconds", 60)
    monkeypatch.setattr(guard.settings, "http_retry_base_seconds", 0)
    calls = 0

    async def failing():
        nonlocal calls
        calls += 1
        raise TimeoutError("upstream timeout")

    with pytest.raises(TimeoutError):
        await guard.run("provider:test", failing, max_attempts=1)
    with pytest.raises(TimeoutError):
        await guard.run("provider:test", failing, max_attempts=1)
    with pytest.raises(CircuitOpenError):
        await guard.run("provider:test", failing, max_attempts=1)
    assert calls == 2


@pytest.mark.asyncio
async def test_provider_guard_retries_transient_failure(monkeypatch):
    guard = ProviderGuard()
    monkeypatch.setattr(guard.settings, "http_retry_base_seconds", 0)
    calls = 0

    async def transient():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("temporary timeout")
        return {"ok": True, "value": 42}

    result = await guard.run("provider:retry", transient, max_attempts=3)
    assert result == {"ok": True, "value": 42, "attempts": 3}


def test_alembic_builds_schema_from_empty_database(tmp_path):
    project_dir = Path(__file__).resolve().parents[1]
    database = tmp_path / "migration.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "DATABASE_AUTO_CREATE": "false",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    import sqlite3

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
    assert {"users", "agent_runs", "attraction_tasks", "alembic_version"}.issubset(tables)
