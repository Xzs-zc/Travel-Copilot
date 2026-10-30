from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import redis_cache_get
from ..config import get_settings
from ..db import AttractionTaskRecord, SessionLocal, utcnow
from ..redis_runtime import get_redis
from ..schemas import AgentEvent
from ..tasks.queue import task_queue


TERMINAL_STATUSES = {"completed", "failed"}


def _loads(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def task_snapshot(row: AttractionTaskRecord) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "poi_id": row.poi_id,
        "status": row.status,
        "attempts": row.attempts,
        "events": _loads(row.events, []),
        "result": _loads(row.result, {}),
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


class DurableAttractionStore:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, poi_id: str) -> AttractionTaskRecord:
        settings = get_settings()
        idempotency_key = f"attraction:{poi_id}"
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing:
            if existing.status == "failed":
                existing.status = "queued"
                existing.attempts = 0
                existing.error = ""
                existing.result = "{}"
                existing.finished_at = None
                existing.updated_at = utcnow()
                events = _loads(existing.events, [])
                events.append(
                    AgentEvent(
                        event="status",
                        data="失败任务已重新加入队列",
                        run_id=existing.job_id,
                    ).model_dump(mode="json")
                )
                existing.events = json.dumps(events, ensure_ascii=False, default=str)
                await self.session.commit()
            return existing
        cached = await redis_cache_get("attraction_detail", f"v24:{settings.ablation_variant}:{poi_id}")
        job_id = uuid.uuid4().hex[:16]
        initial = AgentEvent(event="status", data="景区任务已进入持久化队列", run_id=job_id)
        row = AttractionTaskRecord(
            job_id=job_id,
            poi_id=poi_id,
            idempotency_key=idempotency_key,
            status="completed" if cached else "queued",
            events=json.dumps([initial.model_dump(mode="json")]),
            result=json.dumps(cached or {}, ensure_ascii=False),
            max_attempts=settings.task_max_attempts,
        )
        if cached:
            row.finished_at = utcnow()
        self.session.add(row)
        try:
            await self.session.commit()
            return row
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return existing

    async def get_by_idempotency_key(self, key: str) -> AttractionTaskRecord | None:
        result = await self.session.execute(
            select(AttractionTaskRecord).where(AttractionTaskRecord.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def get(self, job_id: str) -> AttractionTaskRecord | None:
        return await self.session.get(AttractionTaskRecord, job_id)

    async def latest_for_poi(self, poi_id: str) -> AttractionTaskRecord | None:
        result = await self.session.execute(
            select(AttractionTaskRecord)
            .where(AttractionTaskRecord.poi_id == poi_id)
            .order_by(AttractionTaskRecord.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def claim(self, job_id: str) -> AttractionTaskRecord | None:
        result = await self.session.execute(
            select(AttractionTaskRecord)
            .where(AttractionTaskRecord.job_id == job_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None or row.status in TERMINAL_STATUSES or row.status == "running":
            await self.session.rollback()
            return None
        row.status = "running"
        row.attempts += 1
        row.started_at = row.started_at or utcnow()
        row.updated_at = utcnow()
        await self.session.commit()
        return row

    async def append_event(self, job_id: str, event: AgentEvent) -> None:
        row = await self.get(job_id)
        if row is None or row.status in TERMINAL_STATUSES:
            return
        events = _loads(row.events, [])
        events.append(event.model_dump(mode="json"))
        row.events = json.dumps(events, ensure_ascii=False, default=str)
        row.updated_at = utcnow()
        await self.session.commit()

    async def mark_queued(self, job_id: str, error: str = "") -> None:
        row = await self.get(job_id)
        if row is None:
            return
        row.status = "queued"
        row.error = error[:1000]
        row.updated_at = utcnow()
        events = _loads(row.events, [])
        events.append(
            AgentEvent(
                event="status",
                data=f"任务第 {row.attempts} 次执行失败，等待重试",
                run_id=row.job_id,
                meta={"error": error[:300]},
            ).model_dump(mode="json")
        )
        row.events = json.dumps(events, ensure_ascii=False, default=str)
        await self.session.commit()

    async def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        row = await self.get(job_id)
        if row is None:
            return
        row.status = "completed"
        row.result = json.dumps(result, ensure_ascii=False, default=str)
        row.error = ""
        row.updated_at = utcnow()
        row.finished_at = row.updated_at
        await self.session.commit()

    async def mark_failed(self, job_id: str, error: str) -> None:
        row = await self.get(job_id)
        if row is None:
            return
        row.status = "failed"
        row.error = error[:1000]
        row.updated_at = utcnow()
        row.finished_at = row.updated_at
        await self.session.commit()

    async def recover_stale(self, stale_seconds: int) -> list[AttractionTaskRecord]:
        cutoff = utcnow() - timedelta(seconds=stale_seconds)
        result = await self.session.execute(
            select(AttractionTaskRecord).where(
                AttractionTaskRecord.status.in_(("queued", "running")),
                AttractionTaskRecord.updated_at < cutoff,
            )
        )
        rows = list(result.scalars())
        for row in rows:
            row.status = "queued"
            row.updated_at = utcnow()
        await self.session.commit()
        return rows


async def prepare_durable_attraction(poi_id: str) -> dict[str, Any]:
    if get_redis() is None:
        raise RuntimeError("持久化任务队列未启用")
    async with SessionLocal() as session:
        row = await DurableAttractionStore(session).get_or_create(poi_id)
    if row.status == "queued":
        await task_queue.enqueue({"job_id": row.job_id, "poi_id": row.poi_id})
    return task_snapshot(row)


async def stream_durable_attraction_events(poi_id: str) -> AsyncIterator[AgentEvent]:
    settings = get_settings()
    await prepare_durable_attraction(poi_id)
    offset = 0
    while True:
        async with SessionLocal() as session:
            row = await DurableAttractionStore(session).latest_for_poi(poi_id)
        if row is None:
            yield AgentEvent(event="error", data={"code": "task_not_found", "detail": "景区任务不存在"})
            return
        snapshot = task_snapshot(row)
        events = snapshot["events"]
        while offset < len(events):
            yield AgentEvent.model_validate(events[offset])
            offset += 1
        if snapshot["status"] in TERMINAL_STATUSES:
            if snapshot["status"] == "failed":
                yield AgentEvent(
                    event="error",
                    data={"code": "task_failed", "detail": snapshot["error"] or "景区任务执行失败"},
                    run_id=snapshot["job_id"],
                )
            elif snapshot["result"] and not any(event.get("event") == "done" for event in events):
                yield AgentEvent(event="done", data=snapshot["result"], run_id=snapshot["job_id"])
            return
        await asyncio.sleep(settings.task_stream_poll_seconds)


async def enqueue_recoverable_tasks() -> int:
    settings = get_settings()
    async with SessionLocal() as session:
        rows = await DurableAttractionStore(session).recover_stale(settings.task_stale_seconds)
    for row in rows:
        await task_queue.enqueue({"job_id": row.job_id, "poi_id": row.poi_id})
    return len(rows)
