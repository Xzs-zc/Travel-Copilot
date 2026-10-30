from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import get_settings


class RedisUnavailableError(RuntimeError):
    pass


_client: Redis | None = None


async def init_redis(url: str | None = None) -> Redis | None:
    global _client
    redis_url = (url if url is not None else get_settings().redis_url).strip()
    if not redis_url:
        return None
    client = Redis.from_url(
        redis_url,
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=3,
        socket_timeout=15,
    )
    try:
        await client.ping()
    except RedisError as exc:
        await client.aclose()
        raise RedisUnavailableError(f"Redis 不可用：{exc}") from exc
    _client = client
    return client


def get_redis() -> Redis | None:
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def redis_health() -> dict[str, Any]:
    if _client is None:
        return {"enabled": False, "ok": True}
    try:
        latency_ms = await _client.ping()
        return {"enabled": True, "ok": bool(latency_ms)}
    except RedisError as exc:
        return {"enabled": True, "ok": False, "error": str(exc)}
