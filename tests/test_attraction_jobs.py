import asyncio
from contextlib import asynccontextmanager

import pytest

from backend.app.attractions import jobs
from backend.app.attractions.jobs import (
    configure_attraction_jobs,
    prepare_attraction,
    shutdown_attraction_jobs,
    stream_attraction_events,
)
from backend.app.schemas import AgentEvent


class FakeSessionFactory:
    @asynccontextmanager
    async def __call__(self):
        yield object()


class SlowService:
    async def attraction_stream(self, poi_id, trace_context=None):
        yield AgentEvent(event="partial", data={"poi_id": poi_id, "name": "德天瀑布"}, step=1)
        await asyncio.sleep(0.1)
        yield AgentEvent(event="done", data={"poi_id": poi_id, "name": "德天瀑布", "photos": []}, step=2)


@pytest.mark.asyncio
async def test_attraction_job_continues_after_subscriber_disconnects(monkeypatch):
    monkeypatch.setattr(jobs, "SessionLocal", FakeSessionFactory())
    configure_attraction_jobs(lambda session: SlowService())
    try:
        job = prepare_attraction("poi-background")
        stream = stream_attraction_events("poi-background")
        first = await anext(stream)
        assert first.event == "partial"
        await stream.aclose()
        await asyncio.sleep(0.2)
        current = jobs._jobs[job.poi_id]
        assert current.status == "succeeded"
        assert current.result and current.result["name"] == "德天瀑布"

        replay = [event async for event in stream_attraction_events("poi-background")]
        assert replay[0].event == "partial"
        assert replay[-1].event == "done"
    finally:
        await shutdown_attraction_jobs()
