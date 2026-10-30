from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from typing import Any

from .agent.service import TravelAgentService
from .attractions.durable import (
    DurableAttractionStore,
    enqueue_recoverable_tasks,
)
from .config import get_settings
from .db import SessionLocal, init_db
from .mcp.client import McpToolClient
from .redis_runtime import close_redis, init_redis
from .schemas import AgentEvent
from .tasks.queue import task_queue


settings = get_settings()


async def execute_attraction_task(
    job_id: str,
    poi_id: str,
    *,
    mcp_client: McpToolClient,
) -> None:
    while True:
        async with SessionLocal() as session:
            store = DurableAttractionStore(session)
            row = await store.claim(job_id)
            attempts = row.attempts if row else 0
            max_attempts = row.max_attempts if row else settings.task_max_attempts
        if row is None:
            return
        try:
            async with SessionLocal() as session:
                service = TravelAgentService(session, mcp_client=mcp_client)
                async for event in service.attraction_stream(poi_id):
                    if event.event == "error":
                        if attempts >= max_attempts:
                            async with SessionLocal() as event_session:
                                await DurableAttractionStore(event_session).append_event(job_id, event)
                        detail = event.data.get("detail") if isinstance(event.data, dict) else str(event.data)
                        raise RuntimeError(detail or "景区 Agent 执行失败")
                    async with SessionLocal() as event_session:
                        await DurableAttractionStore(event_session).append_event(job_id, event)
                    if event.event == "done":
                        async with SessionLocal() as final_session:
                            await DurableAttractionStore(final_session).mark_completed(
                                job_id,
                                event.data if isinstance(event.data, dict) else {},
                            )
                        return
            raise RuntimeError("景区 Agent 未返回最终结果")
        except Exception as exc:
            if attempts >= max_attempts:
                async with SessionLocal() as failure_session:
                    await DurableAttractionStore(failure_session).mark_failed(job_id, str(exc))
                return
            async with SessionLocal() as retry_session:
                await DurableAttractionStore(retry_session).mark_queued(job_id, str(exc))
            await asyncio.sleep(min(8, 0.5 * (2 ** max(0, attempts - 1))))


async def run_worker() -> None:
    await init_db()
    await init_redis()
    if task_queue.enabled is False:
        raise RuntimeError("Worker 需要配置 REDIS_URL")
    await task_queue.ensure_group()
    recovered = await enqueue_recoverable_tasks()
    if recovered:
        print(f"Recovered {recovered} stale attraction tasks")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    mcp_client = McpToolClient()
    await mcp_client.start()
    last_recovery = time.monotonic()
    try:
        while not stop.is_set():
            await task_queue.heartbeat()
            if time.monotonic() - last_recovery >= settings.task_stale_seconds:
                await enqueue_recoverable_tasks()
                last_recovery = time.monotonic()
            reclaimed = await task_queue.reclaim_stale(min_idle_ms=settings.task_stale_seconds * 1000)
            messages = reclaimed
            if not messages:
                item = await task_queue.read_one(block_ms=5000)
                messages = [item] if item else []
            for message_id, payload in messages:
                await execute_attraction_task(
                    str(payload.get("job_id") or ""),
                    str(payload.get("poi_id") or ""),
                    mcp_client=mcp_client,
                )
                await task_queue.ack(message_id)
    finally:
        await mcp_client.stop()
        await close_redis()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
