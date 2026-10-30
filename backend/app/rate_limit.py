from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass

from fastapi import Request
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .auth import AUTH_COOKIE_NAME
from .config import get_settings
from .errors import error_json_response
from .redis_runtime import get_redis


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class MemoryTokenBucket:
    def __init__(self) -> None:
        self._states: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    async def consume(self, key: str, *, capacity: int, refill_per_second: float) -> RateDecision:
        now = time.monotonic()
        async with self._lock:
            tokens, updated = self._states.get(key, (float(capacity), now))
            tokens = min(float(capacity), tokens + max(0.0, now - updated) * refill_per_second)
            if tokens >= 1:
                tokens -= 1
                allowed = True
            else:
                allowed = False
            retry_after = 0 if allowed else max(1, int((1 - tokens) / refill_per_second))
            self._states[key] = (tokens, now)
            if len(self._states) > 10_000:
                self._states = {k: v for k, v in self._states.items() if now - v[1] < 3600}
            return RateDecision(allowed, capacity, int(tokens), retry_after)


class RedisTokenBucket:
    _SCRIPT = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])
local current = redis.call('HMGET', KEYS[1], 'tokens', 'updated_ms')
local tokens = tonumber(current[1])
local updated = tonumber(current[2])
if tokens == nil or updated == nil then
  tokens = capacity
  updated = now_ms
end
tokens = math.min(capacity, tokens + math.max(0, now_ms - updated) / 1000 * refill)
local allowed = 0
local retry_after = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_after = math.max(1, math.ceil((1 - tokens) / refill))
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated_ms', now_ms)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return {allowed, math.floor(tokens), retry_after}
"""

    async def consume(self, key: str, *, capacity: int, refill_per_second: float) -> RateDecision:
        client = get_redis()
        if client is None:
            raise RedisError("Redis disabled")
        result = await client.eval(
            self._SCRIPT,
            1,
            key,
            capacity,
            refill_per_second,
            int(time.time() * 1000),
            120_000,
        )
        allowed, remaining, retry_after = (int(item) for item in result)
        return RateDecision(bool(allowed), capacity, remaining, retry_after)


class RequestGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.settings = get_settings()
        self.memory = MemoryTokenBucket()
        self.redis = RedisTokenBucket()
        self.redis_retry_at = 0.0

    def _policy(self, path: str) -> tuple[str, int]:
        if path.startswith("/api/auth/"):
            return "auth", self.settings.auth_rate_limit_per_minute
        if path.endswith("/stream"):
            return "stream", self.settings.stream_rate_limit_per_minute
        return "api", self.settings.rate_limit_per_minute

    def _identity(self, request: Request) -> str:
        token = request.cookies.get(AUTH_COOKIE_NAME)
        if token:
            return "session:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        if self.settings.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded:
                return "ip:" + forwarded
        return "ip:" + (request.client.host if request.client else "unknown")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming[:64] if incoming and incoming.isascii() else uuid.uuid4().hex
        request.state.request_id = request_id

        if request.method != "OPTIONS" and request.url.path not in {"/api/health"} and not request.url.path.startswith(("/media/", "/assets/")):
            bucket, limit = self._policy(request.url.path)
            key = f"travel:rate:{bucket}:{self._identity(request)}"
            if time.monotonic() >= self.redis_retry_at:
                try:
                    decision = await self.redis.consume(
                        key,
                        capacity=limit,
                        refill_per_second=max(0.01, limit / 60),
                    )
                except (RedisError, OSError, ValueError):
                    self.redis_retry_at = time.monotonic() + 30
                    decision = await self.memory.consume(
                        key,
                        capacity=limit,
                        refill_per_second=max(0.01, limit / 60),
                    )
            else:
                decision = await self.memory.consume(
                    key,
                    capacity=limit,
                    refill_per_second=max(0.01, limit / 60),
                )
            if not decision.allowed:
                return error_json_response(
                    request,
                    status_code=429,
                    code="rate_limited",
                    message="请求过于频繁，请稍后重试",
                    retryable=True,
                    headers={
                        "Retry-After": str(decision.retry_after),
                        "X-RateLimit-Limit": str(decision.limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
        else:
            decision = None

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if decision is not None:
            response.headers["X-RateLimit-Limit"] = str(decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
