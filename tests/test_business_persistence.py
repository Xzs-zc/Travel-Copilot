import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.attractions.durable import DurableAttractionStore, task_snapshot
from backend.app.cache import cache_compute_lock, redis_cache_delete, redis_cache_get, redis_cache_set
from backend.app.config import get_settings
from backend.app.db import (
    AttractionTaskRecord,
    SessionLocal,
    SupportThreadRecord,
    UserRecord,
)
from backend.app.rate_limit import RedisTokenBucket
from backend.app.redis_runtime import close_redis, get_redis, init_redis
from backend.app.schemas import AgentEvent
from backend.app.tasks.queue import RedisTaskQueue


RUN_ID = uuid.uuid4().hex[:10]
PREFIX = f"resume-it-{RUN_ID}"


async def _cleanup_records() -> None:
    async with SessionLocal() as session:
        await session.execute(
            delete(AttractionTaskRecord).where(AttractionTaskRecord.poi_id.like(f"{PREFIX}%"))
        )
        await session.execute(
            delete(SupportThreadRecord).where(SupportThreadRecord.title.like(f"{PREFIX}%"))
        )
        await session.execute(delete(UserRecord).where(UserRecord.username.like(f"{PREFIX}%")))
        await session.commit()


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def live_infrastructure():
    if os.getenv("RUN_LIVE_INFRA_TESTS") != "1":
        pytest.skip("设置 RUN_LIVE_INFRA_TESTS=1 后运行真实 PostgreSQL / Redis 测试")
    await init_redis()
    if get_redis() is None:
        pytest.fail("Redis 未连接")
    async with SessionLocal() as session:
        await session.execute(select(1))
    try:
        yield
    finally:
        await _cleanup_records()
        client = get_redis()
        if client is not None:
            keys = [
                key
                async for key in client.scan_iter(match=f"travel:eval:{PREFIX}*")
            ]
            if keys:
                await client.delete(*keys)
        await close_redis()


@pytest.mark.asyncio(loop_scope="module")
async def test_duplicate_attraction_clicks_create_one_durable_job():
    poi_id = f"{PREFIX}-double-click"

    async def submit_once() -> str:
        async with SessionLocal() as session:
            row = await DurableAttractionStore(session).get_or_create(poi_id)
            return row.job_id

    job_ids = await asyncio.gather(*(submit_once() for _ in range(12)))

    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AttractionTaskRecord)
            .where(AttractionTaskRecord.idempotency_key == f"attraction:{poi_id}")
        )

    assert len(set(job_ids)) == 1
    assert count == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_stale_worker_task_is_requeued_and_can_be_retried():
    poi_id = f"{PREFIX}-stale-worker"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        claimed = await store.claim(task.job_id)
        assert claimed is not None
        claimed.updated_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        await session.commit()
        recovered = await store.recover_stale(stale_seconds=1)
        assert any(row.job_id == task.job_id and row.status == "queued" for row in recovered)
        retried = await store.claim(task.job_id)
        assert retried is not None
        assert retried.job_id == task.job_id
        assert retried.attempts == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_attraction_task_survives_database_connection_restart():
    poi_id = f"{PREFIX}-db-restart"
    database_url = get_settings().database_url
    first_engine = create_async_engine(database_url)
    first_sessions = async_sessionmaker(first_engine, expire_on_commit=False)
    async with first_sessions() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        await store.claim(task.job_id)
        await store.append_event(
            task.job_id,
            AgentEvent(event="tool_result", data={"source": "12306", "ok": True}, run_id=task.job_id),
        )
        await store.mark_completed(task.job_id, {"route": "南京-北京", "status": "planned"})
        job_id = task.job_id
    await first_engine.dispose()

    second_engine = create_async_engine(database_url)
    second_sessions = async_sessionmaker(second_engine, expire_on_commit=False)
    async with second_sessions() as session:
        row = await DurableAttractionStore(session).get(job_id)
        assert row is not None
        snapshot = task_snapshot(row)
    await second_engine.dispose()

    assert snapshot["status"] == "completed"
    assert snapshot["attempts"] == 1
    assert snapshot["result"] == {"route": "南京-北京", "status": "planned"}
    assert snapshot["events"][-1]["data"]["source"] == "12306"


@pytest.mark.asyncio(loop_scope="module")
async def test_sse_reconnect_replays_events_after_last_cursor():
    poi_id = f"{PREFIX}-sse-replay"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        await store.claim(task.job_id)
        await store.append_event(
            task.job_id,
            AgentEvent(event="status", data="正在核验景区开放信息", run_id=task.job_id),
        )
        await store.append_event(
            task.job_id,
            AgentEvent(event="tool_result", data={"tool": "official_site", "ok": True}, run_id=task.job_id),
        )
        await store.append_event(
            task.job_id,
            AgentEvent(event="status", data="正在生成交通方案", run_id=task.job_id),
        )
        await store.mark_completed(task.job_id, {"route": "南京-杭州"})
        events = task_snapshot(await store.get(task.job_id))["events"]

    last_received_cursor = next(
        index + 1 for index, event in enumerate(events) if event["event"] == "tool_result"
    )
    replayed = events[last_received_cursor:]

    assert [event["event"] for event in replayed] == ["status"]
    assert replayed[0]["data"] == "正在生成交通方案"


@pytest.mark.asyncio(loop_scope="module")
async def test_postgres_tenant_cannot_read_another_users_support_thread():
    user_a = f"{PREFIX}-a"[:32]
    user_b = f"{PREFIX}-b"[:32]
    thread_id = uuid.uuid4().hex[:32]
    async with SessionLocal() as session:
        session.add_all(
            [
                UserRecord(
                    id=user_a,
                    username=f"{PREFIX}-a",
                    password_hash="x" * 128,
                    password_salt="x" * 64,
                ),
                UserRecord(
                    id=user_b,
                    username=f"{PREFIX}-b",
                    password_hash="x" * 128,
                    password_salt="x" * 64,
                ),
                SupportThreadRecord(
                    id=thread_id,
                    user_id=user_a,
                    title=f"{PREFIX}-tenant-isolation",
                ),
            ]
        )
        await session.commit()

        own = await session.get(SupportThreadRecord, thread_id)
        other = await session.scalar(
            select(SupportThreadRecord).where(
                SupportThreadRecord.id == thread_id,
                SupportThreadRecord.user_id == user_b,
            )
        )

    assert own is not None
    assert own.user_id == user_a
    assert other is None


@pytest.mark.asyncio(loop_scope="module")
async def test_late_worker_event_cannot_corrupt_completed_attraction_result():
    poi_id = f"{PREFIX}-late-event"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        await store.claim(task.job_id)
        await store.mark_completed(task.job_id, {"route": "南京-苏州"})
        await store.append_event(
            task.job_id,
            AgentEvent(event="tool_result", data={"late": True}, run_id=task.job_id),
        )
        snapshot = task_snapshot(await store.get(task.job_id))

    assert snapshot["status"] == "completed"
    assert snapshot["result"] == {"route": "南京-苏州"}
    assert all(event.get("data") != {"late": True} for event in snapshot["events"])


@pytest.mark.asyncio(loop_scope="module")
async def test_completed_attraction_task_cannot_be_claimed_again():
    poi_id = f"{PREFIX}-completed-claim"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        job_id = task.job_id
        assert await store.claim(job_id) is not None
        await store.mark_completed(job_id, {"route": "南京-扬州"})
        assert await store.claim(job_id) is None

    async with SessionLocal() as verify_session:
        row = await DurableAttractionStore(verify_session).get(job_id)
        assert row is not None
        snapshot = task_snapshot(row)
        assert row.status == "completed"
        assert row.attempts == 1
        assert snapshot["result"] == {"route": "南京-扬州"}


@pytest.mark.asyncio(loop_scope="module")
async def test_failed_terminal_attraction_task_cannot_be_claimed_again():
    poi_id = f"{PREFIX}-failed-claim"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        job_id = task.job_id
        await store.claim(job_id)
        await store.mark_failed(job_id, "景区官网连续超时")
        assert await store.claim(job_id) is None

    async with SessionLocal() as verify_session:
        row = await DurableAttractionStore(verify_session).get(job_id)
        assert row is not None
        assert row.status == "failed"
        assert row.error == "景区官网连续超时"
        assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="module")
async def test_failed_attraction_request_is_requeued_with_same_job_id():
    poi_id = f"{PREFIX}-failed-reopen"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        original_job_id = task.job_id
        await store.claim(task.job_id)
        await store.mark_failed(task.job_id, "12306 查询失败")
        reopened = await store.get_or_create(poi_id)

    assert reopened.job_id == original_job_id
    assert reopened.status == "queued"
    assert reopened.attempts == 0
    assert reopened.error == ""
    assert reopened.result == "{}"
    assert reopened.finished_at is None


@pytest.mark.asyncio(loop_scope="module")
async def test_fresh_queued_task_is_not_recovered_as_stale():
    poi_id = f"{PREFIX}-fresh-stale"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        recovered = await store.recover_stale(stale_seconds=3600)

    assert all(row.job_id != task.job_id for row in recovered)
    assert task.status == "queued"


@pytest.mark.asyncio(loop_scope="module")
async def test_two_pois_create_independent_durable_jobs():
    first_poi = f"{PREFIX}-independent-a"
    second_poi = f"{PREFIX}-independent-b"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        first = await store.get_or_create(first_poi)
        second = await store.get_or_create(second_poi)

    assert first.job_id != second.job_id
    assert first.poi_id == first_poi
    assert second.poi_id == second_poi


@pytest.mark.asyncio(loop_scope="module")
async def test_only_one_concurrent_worker_can_claim_attraction_job():
    poi_id = f"{PREFIX}-single-claim"
    async with SessionLocal() as session:
        task = await DurableAttractionStore(session).get_or_create(poi_id)
        job_id = task.job_id

    async def claim_once():
        async with SessionLocal() as session:
            return await DurableAttractionStore(session).claim(job_id)

    claims = await asyncio.gather(*(claim_once() for _ in range(8)))

    assert sum(claim is not None for claim in claims) == 1
    async with SessionLocal() as session:
        row = await DurableAttractionStore(session).get(job_id)
        assert row is not None
        assert row.status == "running"
        assert row.attempts == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_retry_attempt_increments_while_started_at_stays_stable():
    poi_id = f"{PREFIX}-attempt-history"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        first = await store.claim(task.job_id)
        assert first is not None
        started_at = first.started_at
        await store.mark_queued(task.job_id, "高德接口超时")
        second = await store.claim(task.job_id)

    assert second is not None
    assert second.attempts == 2
    assert second.started_at == started_at


@pytest.mark.asyncio(loop_scope="module")
async def test_many_worker_events_persist_in_execution_order():
    poi_id = f"{PREFIX}-event-order"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        await store.claim(task.job_id)
        for sequence in range(20):
            await store.append_event(
                task.job_id,
                AgentEvent(
                    event="tool_result",
                    data={"sequence": sequence, "tool": "attraction_lookup"},
                    run_id=task.job_id,
                ),
            )
        events = task_snapshot(await store.get(task.job_id))["events"]

    tool_events = [event for event in events if event["event"] == "tool_result"]
    assert [event["data"]["sequence"] for event in tool_events] == list(range(20))


@pytest.mark.asyncio(loop_scope="module")
async def test_corrupt_event_and_result_json_do_not_break_task_snapshot():
    poi_id = f"{PREFIX}-corrupt-json"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        task.events = "{broken-events"
        task.result = "{broken-result"
        await session.commit()
        snapshot = task_snapshot(await store.get(task.job_id))

    assert snapshot["events"] == []
    assert snapshot["result"] == {}


@pytest.mark.asyncio(loop_scope="module")
async def test_completion_clears_previous_error_and_sets_finished_at():
    poi_id = f"{PREFIX}-completion-cleanup"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        await store.claim(task.job_id)
        await store.mark_queued(task.job_id, "第一次查询超时")
        await store.claim(task.job_id)
        await store.mark_completed(task.job_id, {"route": "南京-黄山"})

    async with SessionLocal() as verify_session:
        row = await DurableAttractionStore(verify_session).get(task.job_id)
        assert row is not None
        snapshot = task_snapshot(row)
        assert snapshot["status"] == "completed"
        assert snapshot["error"] == ""
        assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="module")
async def test_retry_queue_records_diagnostic_error_event():
    poi_id = f"{PREFIX}-retry-diagnostic"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        await store.claim(task.job_id)
        await store.mark_queued(task.job_id, "12306 查询超时")
        events = task_snapshot(await store.get(task.job_id))["events"]

    retry_event = events[-1]
    assert retry_event["event"] == "status"
    assert retry_event["meta"]["error"] == "12306 查询超时"
    assert "等待重试" in retry_event["data"]


@pytest.mark.asyncio(loop_scope="module")
async def test_new_attraction_task_has_auditable_initial_status_event():
    poi_id = f"{PREFIX}-initial-audit"
    async with SessionLocal() as session:
        task = await DurableAttractionStore(session).get_or_create(poi_id)
        snapshot = task_snapshot(task)

    assert snapshot["status"] == "queued"
    assert snapshot["events"][0]["event"] == "status"
    assert snapshot["events"][0]["run_id"] == task.job_id


@pytest.mark.asyncio(loop_scope="module")
async def test_mark_failed_records_terminal_error_and_finish_time():
    poi_id = f"{PREFIX}-terminal-failure"
    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        task = await store.get_or_create(poi_id)
        await store.claim(task.job_id)
        await store.mark_failed(task.job_id, "连续三次无法获取景区官网")

    async with SessionLocal() as verify_session:
        row = await DurableAttractionStore(verify_session).get(task.job_id)
        assert row is not None
        snapshot = task_snapshot(row)
        assert snapshot["status"] == "failed"
        assert snapshot["error"] == "连续三次无法获取景区官网"
        assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="module")
async def test_duplicate_username_is_rejected_at_database_boundary():
    username = f"{PREFIX}-duplicate-user"
    async with SessionLocal() as session:
        session.add(
            UserRecord(
                id=uuid.uuid4().hex[:16],
                username=username,
                password_hash="x" * 128,
                password_salt="x" * 64,
            )
        )
        await session.commit()

    with pytest.raises(IntegrityError):
        async with SessionLocal() as duplicate_session:
            duplicate_session.add(
                UserRecord(
                    id=uuid.uuid4().hex[:16],
                    username=username,
                    password_hash="y" * 128,
                    password_salt="y" * 64,
                )
            )
            await duplicate_session.commit()


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_consumer_group_delivers_job_once_and_ack_clears_pending():
    client = get_redis()
    assert client is not None
    stream = f"travel:eval:{PREFIX}:single-delivery"
    group = f"group:{RUN_ID}"
    first = RedisTaskQueue()
    first.stream, first.group, first.consumer = stream, group, "worker-a"
    second = RedisTaskQueue()
    second.stream, second.group, second.consumer = stream, group, "worker-b"
    await first.ensure_group()
    await first.enqueue({"job_id": f"{PREFIX}-job", "poi_id": f"{PREFIX}-redis"})
    received = await asyncio.gather(
        first.read_one(block_ms=100),
        second.read_one(block_ms=100),
    )
    winners = [(queue, item) for queue, item in zip((first, second), received) if item is not None]
    assert len(winners) == 1
    winner, (message_id, payload) = winners[0]
    pending_before = await client.xpending_range(stream, group, "-", "+", 10)
    assert len(pending_before) == 1
    await winner.ack(message_id)
    pending_after = await client.xpending_range(stream, group, "-", "+", 10)
    assert pending_after == []
    assert payload["poi_id"] == f"{PREFIX}-redis"
    await client.delete(stream)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_unacked_job_is_reclaimed_after_worker_crash():
    client = get_redis()
    assert client is not None
    stream = f"travel:eval:{PREFIX}:reclaim"
    group = f"group:{RUN_ID}"
    crashed = RedisTaskQueue()
    crashed.stream, crashed.group, crashed.consumer = stream, group, "worker-crashed"
    replacement = RedisTaskQueue()
    replacement.stream, replacement.group, replacement.consumer = stream, group, "worker-replacement"
    await crashed.ensure_group()
    await crashed.enqueue({"job_id": f"{PREFIX}-recover", "poi_id": f"{PREFIX}-stale"})
    received = await crashed.read_one(block_ms=100)
    assert received is not None

    reclaimed = await replacement.reclaim_stale(min_idle_ms=0, count=10)

    assert len(reclaimed) == 1
    assert reclaimed[0][1]["job_id"] == f"{PREFIX}-recover"
    await replacement.ack(reclaimed[0][0])
    assert await client.xpending_range(stream, group, "-", "+", 10) == []
    await client.delete(stream)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_cache_hit_returns_completed_attraction_and_miss_queues_task():
    settings = get_settings()
    hit_poi = f"{PREFIX}-cache-hit"
    miss_poi = f"{PREFIX}-cache-miss"
    cached = {"name": "杭州西湖", "open_status": "official", "source": "官网"}
    await redis_cache_set(
        "attraction_detail",
        f"v24:{settings.ablation_variant}:{hit_poi}",
        cached,
        60,
    )

    async with SessionLocal() as session:
        store = DurableAttractionStore(session)
        hit = await store.get_or_create(hit_poi)
        miss = await store.get_or_create(miss_poi)

    assert hit.status == "completed"
    assert task_snapshot(hit)["result"] == cached
    assert miss.status == "queued"


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_cache_ttl_expiry_forces_fresh_attraction_lookup():
    scope = f"resume-test:{RUN_ID}"
    await redis_cache_set(scope, "wikipedia-page", {"revision": "old"}, ttl_seconds=1)
    assert await redis_cache_get(scope, "wikipedia-page") == {"revision": "old"}
    await asyncio.sleep(1.1)
    assert await redis_cache_get(scope, "wikipedia-page") is None
    assert await redis_cache_delete(scope, "wikipedia-page") is False


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_duplicate_ack_is_safe_for_retried_worker():
    client = get_redis()
    assert client is not None
    stream = f"travel:eval:{PREFIX}:duplicate-ack"
    group = f"group:{RUN_ID}"
    queue = RedisTaskQueue()
    queue.stream, queue.group, queue.consumer = stream, group, "worker-retry"
    await queue.ensure_group()
    await queue.enqueue({"job_id": f"{PREFIX}-ack", "poi_id": f"{PREFIX}-ack"})
    received = await queue.read_one(block_ms=100)
    assert received is not None
    message_id, _ = received
    await queue.ack(message_id)
    await queue.ack(message_id)
    assert await client.xpending_range(stream, group, "-", "+", 10) == []
    await client.delete(stream)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_stream_preserves_travel_job_arrival_order():
    client = get_redis()
    assert client is not None
    stream = f"travel:eval:{PREFIX}:order"
    group = f"group:{RUN_ID}"
    queue = RedisTaskQueue()
    queue.stream, queue.group, queue.consumer = stream, group, "worker-order"
    await queue.ensure_group()
    for suffix in ("first", "second", "third"):
        await queue.enqueue({"job_id": f"{PREFIX}-{suffix}", "poi_id": suffix})
    received = [
        await queue.read_one(block_ms=100)
        for _ in range(3)
    ]
    assert all(item is not None for item in received)
    assert [item[1]["poi_id"] for item in received if item is not None] == [
        "first",
        "second",
        "third",
    ]
    for item in received:
        assert item is not None
        await queue.ack(item[0])
    await client.delete(stream)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_stream_preserves_unicode_poi_payload():
    client = get_redis()
    assert client is not None
    stream = f"travel:eval:{PREFIX}:unicode"
    group = f"group:{RUN_ID}"
    queue = RedisTaskQueue()
    queue.stream, queue.group, queue.consumer = stream, group, "worker-unicode"
    await queue.ensure_group()
    await queue.enqueue({"job_id": f"{PREFIX}-unicode", "poi_id": "北京·故宫博物院"})
    received = await queue.read_one(block_ms=100)
    assert received is not None
    assert received[1]["poi_id"] == "北京·故宫博物院"
    await queue.ack(received[0])
    await client.delete(stream)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_reclaim_waits_until_worker_lease_is_stale():
    client = get_redis()
    assert client is not None
    stream = f"travel:eval:{PREFIX}:lease-threshold"
    group = f"group:{RUN_ID}"
    owner = RedisTaskQueue()
    owner.stream, owner.group, owner.consumer = stream, group, "worker-owner"
    replacement = RedisTaskQueue()
    replacement.stream, replacement.group, replacement.consumer = stream, group, "worker-replacement"
    await owner.ensure_group()
    await owner.enqueue({"job_id": f"{PREFIX}-lease", "poi_id": f"{PREFIX}-lease"})
    assert await owner.read_one(block_ms=100) is not None
    assert await replacement.reclaim_stale(min_idle_ms=60_000, count=10) == []
    reclaimed = await replacement.reclaim_stale(min_idle_ms=0, count=10)
    assert len(reclaimed) == 1
    await replacement.ack(reclaimed[0][0])
    await client.delete(stream)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_acked_job_is_not_reclaimed_again():
    client = get_redis()
    assert client is not None
    stream = f"travel:eval:{PREFIX}:acked-reclaim"
    group = f"group:{RUN_ID}"
    queue = RedisTaskQueue()
    queue.stream, queue.group, queue.consumer = stream, group, "worker-a"
    replacement = RedisTaskQueue()
    replacement.stream, replacement.group, replacement.consumer = stream, group, "worker-b"
    await queue.ensure_group()
    await queue.enqueue({"job_id": f"{PREFIX}-acked", "poi_id": f"{PREFIX}-acked"})
    received = await queue.read_one(block_ms=100)
    assert received is not None
    await queue.ack(received[0])
    assert await replacement.reclaim_stale(min_idle_ms=0, count=10) == []
    await client.delete(stream)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_cache_delete_invalidates_stale_scenic_data():
    scope = f"resume-delete:{RUN_ID}"
    await redis_cache_set(scope, "poi", {"open_status": "open"}, ttl_seconds=60)
    assert await redis_cache_delete(scope, "poi") is True
    assert await redis_cache_get(scope, "poi") is None
    assert await redis_cache_delete(scope, "poi") is False


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_malformed_cache_payload_is_treated_as_miss():
    client = get_redis()
    assert client is not None
    scope = f"resume-corrupt:{RUN_ID}"
    key = "poi"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cache_key = f"travel:cache:{scope}:{digest}"
    await client.set(cache_key, "{broken-cache-json", ex=30)
    assert await redis_cache_get(scope, key) is None
    await client.delete(cache_key)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_cache_refresh_replaces_value_and_extends_ttl():
    client = get_redis()
    assert client is not None
    scope = f"resume-refresh:{RUN_ID}"
    key = "attraction"
    assert await redis_cache_set(scope, key, {"open_status": "unknown"}, ttl_seconds=2)
    assert await redis_cache_set(scope, key, {"open_status": "official"}, ttl_seconds=20)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    ttl = await client.ttl(f"travel:cache:{scope}:{digest}")
    assert await redis_cache_get(scope, key) == {"open_status": "official"}
    assert 15 <= ttl <= 20
    await redis_cache_delete(scope, key)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_rate_limit_blocks_concurrent_travel_api_burst():
    client = get_redis()
    assert client is not None
    limiter = RedisTokenBucket()
    key = f"travel:rate:eval:{PREFIX}:burst"
    decisions = await asyncio.gather(
        *(
            limiter.consume(key, capacity=3, refill_per_second=0.001)
            for _ in range(8)
        )
    )
    assert sum(decision.allowed for decision in decisions) == 3
    assert sum(not decision.allowed for decision in decisions) == 5
    await client.delete(key)


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_cache_fill_lock_serializes_same_scenic_query():
    active = 0
    max_active = 0
    completed = 0

    async def fill_cache():
        nonlocal active, max_active, completed
        async with cache_compute_lock("test", f"{PREFIX}-same-poi", ttl_seconds=5) as acquired:
            assert acquired
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            completed += 1
            active -= 1

    await asyncio.gather(*(fill_cache() for _ in range(6)))

    assert max_active == 1
    assert completed == 6


@pytest.mark.asyncio(loop_scope="module")
async def test_redis_business_streams_do_not_cross_consume_jobs():
    client = get_redis()
    assert client is not None
    group = f"group:{RUN_ID}"
    attraction = RedisTaskQueue()
    attraction.stream, attraction.group, attraction.consumer = (
        f"travel:eval:{PREFIX}:attractions",
        group,
        "attraction-worker",
    )
    support = RedisTaskQueue()
    support.stream, support.group, support.consumer = (
        f"travel:eval:{PREFIX}:support",
        group,
        "support-worker",
    )
    await attraction.ensure_group()
    await support.ensure_group()
    await attraction.enqueue({"job_id": f"{PREFIX}-attraction", "poi_id": "南京博物院"})
    await support.enqueue({"job_id": f"{PREFIX}-support", "poi_id": "订单咨询"})
    attraction_item = await attraction.read_one(block_ms=100)
    support_item = await support.read_one(block_ms=100)
    assert attraction_item is not None
    assert support_item is not None
    assert attraction_item[1]["poi_id"] == "南京博物院"
    assert support_item[1]["poi_id"] == "订单咨询"
    await attraction.ack(attraction_item[0])
    await support.ack(support_item[0])
    await client.delete(attraction.stream, support.stream)
