from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from ..db import SessionLocal
from ..observability import finish_trace, score_trace, start_trace, trace_value, update
from ..schemas import AgentEvent

JobStatus = Literal["queued", "running", "succeeded", "failed"]
ServiceFactory = Callable[[Any], Any]
_JOB_TTL_SECONDS = 30 * 60

@dataclass
class AttractionJob:
    poi_id: str
    job_id: str
    status: JobStatus = "queued"
    events: list[AgentEvent] = field(default_factory=list)
    subscribers: set[asyncio.Queue[None]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def is_terminal(self) -> bool:
        return self.status in {"succeeded", "failed"}


_jobs: dict[str, AttractionJob] = {}
_service_factory: ServiceFactory | None = None


def configure_attraction_jobs(factory: ServiceFactory) -> None:
    global _service_factory
    _service_factory = factory


def _prune_jobs() -> None:
    now = time.time()
    for poi_id, job in list(_jobs.items()):
        if job.is_terminal() and now - job.updated_at > _JOB_TTL_SECONDS:
            _jobs.pop(poi_id, None)


def _publish(job: AttractionJob, event: AgentEvent) -> None:
    job.events.append(event)
    job.updated_at = time.time()
    for queue in list(job.subscribers):
        queue.put_nowait(None)


def prepare_attraction(poi_id: str) -> AttractionJob:
    _prune_jobs()
    existing = _jobs.get(poi_id)
    if existing and not (existing.is_terminal() and time.time() - existing.updated_at > _JOB_TTL_SECONDS):
        if not existing.is_terminal() or existing.status == "succeeded":
            return existing
    job = AttractionJob(poi_id=poi_id, job_id=uuid.uuid4().hex[:16])
    _jobs[poi_id] = job
    job.task = asyncio.create_task(_run_job(job), name=f"attraction-job:{poi_id}")
    return job


def attraction_job_status(poi_id: str) -> dict[str, Any] | None:
    job = _jobs.get(poi_id)
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "poi_id": job.poi_id,
        "status": job.status,
        "error": job.error,
        "event_count": len(job.events),
        "has_result": job.result is not None,
    }


async def _run_job(job: AttractionJob) -> None:
    job.status = "running"
    handle = start_trace(
        name=f"attraction:{job.poi_id}",
        input={"poi_id": job.poi_id, "job_id": job.job_id},
        metadata={"feature": "attraction", "poi_id": job.poi_id, "job_id": job.job_id, "background": True},
        tags=["attraction", "travel-copilot", "background"],
    )
    final: dict[str, Any] | None = None
    try:
        if _service_factory is None:
            raise RuntimeError("景区后台任务尚未配置 Service Factory")
        async with SessionLocal() as session:
            service = _service_factory(session)
            async with aclosing(service.attraction_stream(job.poi_id, trace_context=handle.context)) as stream:
                async for event in stream:
                    _publish(job, event)
                    if event.event == "done" and isinstance(event.data, dict):
                        final = event.data
                        job.result = final
                        job.status = "succeeded"
                        break
                    if event.event == "error":
                        job.error = str(event.data)
                        job.status = "failed"
                        break
    except asyncio.CancelledError:
        job.status = "failed"
        job.error = "景区后台任务已取消"
        _publish(job, AgentEvent(event="error", data=job.error, run_id=job.job_id))
        raise
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        _publish(job, AgentEvent(event="error", data=str(exc), run_id=job.job_id))
        update(handle.observation, level="ERROR", status_message=str(exc))
    finally:
        if job.status == "running":
            job.status = "failed"
            job.error = job.error or "景区后台任务未产生结果"
        success = job.status == "succeeded"
        if final:
            images = final.get("photos") or []
            agent = final.get("agent") or {}
            score_trace(handle, "task_success", 1, data_type="BOOLEAN")
            score_trace(handle, "image_count", len(images), data_type="NUMERIC")
            score_trace(handle, "official_verified", 1 if final.get("open_status") == "official" else 0, data_type="BOOLEAN")
            score_trace(handle, "sub_spot_count", len(final.get("sub_spots") or []), data_type="NUMERIC")
            score_trace(handle, "tool_calls", int(agent.get("tool_calls") or 0), data_type="NUMERIC")
            score_trace(handle, "replan_count", int(agent.get("replan_count") or 0), data_type="NUMERIC")
        else:
            score_trace(handle, "task_success", 0, data_type="BOOLEAN")
        finish_trace(handle, output=trace_value({"success": success, "poi_id": job.poi_id, "result": final}))
        job.updated_at = time.time()
        for queue in list(job.subscribers):
            queue.put_nowait(None)


async def stream_attraction_events(poi_id: str):
    job = prepare_attraction(poi_id)
    queue: asyncio.Queue[None] = asyncio.Queue()
    job.subscribers.add(queue)
    index = 0
    try:
        while True:
            while index < len(job.events):
                event = job.events[index]
                index += 1
                yield event
                if event.event in {"done", "error"}:
                    return
            if job.is_terminal():
                return
            await queue.get()
    finally:
        job.subscribers.discard(queue)


async def shutdown_attraction_jobs() -> None:
    tasks = [job.task for job in _jobs.values() if job.task and not job.task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _jobs.clear()
