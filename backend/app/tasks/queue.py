from __future__ import annotations

from typing import Any

from redis.exceptions import ResponseError

from ..config import get_settings
from ..redis_runtime import get_redis


class RedisTaskQueue:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.stream = self.settings.task_queue_stream
        self.group = self.settings.task_queue_group
        self.consumer = self.settings.task_consumer_name

    @property
    def enabled(self) -> bool:
        return get_redis() is not None

    async def ensure_group(self) -> None:
        client = get_redis()
        if client is None:
            raise RuntimeError("Redis 未配置")
        try:
            await client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, payload: dict[str, str]) -> str:
        client = get_redis()
        if client is None:
            raise RuntimeError("Redis 未配置")
        message_id = await client.xadd(
            self.stream,
            payload,
            maxlen=10_000,
            approximate=True,
        )
        return str(message_id)

    async def read_one(self, *, block_ms: int = 5000) -> tuple[str, dict[str, str]] | None:
        client = get_redis()
        if client is None:
            return None
        result = await client.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: ">"},
            count=1,
            block=block_ms,
        )
        if not result:
            return None
        _, messages = result[0]
        if not messages:
            return None
        message_id, payload = messages[0]
        return str(message_id), {str(k): str(v) for k, v in payload.items()}

    async def reclaim_stale(self, *, min_idle_ms: int, count: int = 10) -> list[tuple[str, dict[str, str]]]:
        client = get_redis()
        if client is None:
            return []
        result = await client.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        messages = result[1] if len(result) > 1 else []
        return [
            (str(message_id), {str(k): str(v) for k, v in payload.items()})
            for message_id, payload in messages
            if payload
        ]

    async def ack(self, message_id: str) -> None:
        client = get_redis()
        if client is not None:
            await client.xack(self.stream, self.group, message_id)

    async def heartbeat(self, ttl_seconds: int = 30) -> None:
        client = get_redis()
        if client is not None:
            await client.set("travel:worker:heartbeat", self.consumer, ex=ttl_seconds)


task_queue = RedisTaskQueue()
