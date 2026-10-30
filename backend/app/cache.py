from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from redis.exceptions import LockError, RedisError

from .redis_runtime import get_redis

_local_key_lock: "LocalKeyLock"


def _cache_key(scope: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"travel:cache:{scope}:{digest}"


async def redis_cache_get(scope: str, key: str) -> Any | None:
    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(_cache_key(scope, key))
        return json.loads(raw) if raw is not None else None
    except (RedisError, json.JSONDecodeError):
        return None


async def redis_cache_set(scope: str, key: str, value: Any, ttl_seconds: int) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        await client.set(
            _cache_key(scope, key),
            json.dumps(value, ensure_ascii=False, default=str),
            ex=max(1, ttl_seconds),
        )
        return True
    except RedisError:
        return False


async def redis_cache_delete(scope: str, key: str) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(await client.delete(_cache_key(scope, key)))
    except RedisError:
        return False


@asynccontextmanager
async def cache_compute_lock(scope: str, key: str, *, ttl_seconds: int = 15) -> AsyncIterator[bool]:
    client = get_redis()
    if client is not None:
        lock_key = f"travel:lock:cache:{scope}:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
        lock = client.lock(lock_key, timeout=ttl_seconds, blocking_timeout=ttl_seconds)
        acquired = False
        try:
            acquired = bool(await lock.acquire(blocking=True))
        except (RedisError, LockError):
            pass
        else:
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        await lock.release()
                    except (RedisError, LockError):
                        pass
            return

    async with _local_key_lock.hold(f"{scope}:{key}", wait_seconds=ttl_seconds) as acquired:
        yield acquired


class LocalKeyLock:
    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[str, tuple[asyncio.Lock, float]] = {}

    @asynccontextmanager
    async def hold(self, key: str, wait_seconds: float = 15) -> AsyncIterator[bool]:
        async with self._guard:
            lock, _ = self._locks.get(key, (asyncio.Lock(), time.monotonic()))
            self._locks[key] = (lock, time.monotonic())
        acquired = False
        try:
            await asyncio.wait_for(lock.acquire(), timeout=wait_seconds)
            acquired = True
            yield True
        except TimeoutError:
            yield False
        finally:
            if acquired:
                lock.release()


_local_key_lock = LocalKeyLock()
