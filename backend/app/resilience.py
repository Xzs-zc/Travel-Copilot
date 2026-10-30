from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import get_settings


class CircuitOpenError(RuntimeError):
    pass


class RetryableOperationError(RuntimeError):
    pass


@dataclass
class CircuitState:
    failures: int = 0
    opened_until: float = 0
    half_open_trial: bool = False


class ProviderGuard:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._states: dict[str, CircuitState] = {}
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def _semaphore(self, key: str) -> asyncio.Semaphore:
        async with self._lock:
            return self._sems.setdefault(key, asyncio.Semaphore(self.settings.provider_max_concurrency))

    async def _before_call(self, key: str) -> None:
        async with self._lock:
            state = self._states.setdefault(key, CircuitState())
            now = time.monotonic()
            if state.opened_until > now:
                raise CircuitOpenError(f"{key} 熔断中，{state.opened_until - now:.1f}s 后重试")
            if state.opened_until and state.opened_until <= now:
                if state.half_open_trial:
                    raise CircuitOpenError(f"{key} 正在进行半开探测")
                state.half_open_trial = True

    async def _record_success(self, key: str) -> None:
        async with self._lock:
            self._states[key] = CircuitState()

    async def _record_failure(self, key: str) -> None:
        async with self._lock:
            state = self._states.setdefault(key, CircuitState())
            state.failures += 1
            state.half_open_trial = False
            if state.failures >= self.settings.provider_failure_threshold:
                state.opened_until = time.monotonic() + self.settings.provider_recovery_seconds

    @staticmethod
    def _retryable(value: Any, exc: Exception | None = None) -> bool:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError, OSError)):
            return True
        text = str(exc or value).lower()
        return any(token in text for token in ("429", "500", "502", "503", "504", "timeout", "temporar", "connection", "超时"))

    async def run(
        self,
        key: str,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        *,
        max_attempts: int | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        semaphore = await self._semaphore(key)
        attempts = max_attempts or self.settings.http_max_attempts
        async with semaphore:
            await self._before_call(key)
            for attempt in range(1, attempts + 1):
                try:
                    operation_task = asyncio.create_task(operation())
                    result = await asyncio.wait_for(
                        operation_task,
                        timeout=timeout_seconds or self.settings.http_timeout_seconds,
                    )
                    if isinstance(result, dict) and result.get("ok") is False:
                        error = str(result.get("error") or "")
                        if self._retryable(error) and attempt < attempts:
                            raise RetryableOperationError(error)
                        await self._record_success(key)
                        result.setdefault("attempts", attempt)
                        return result
                    await self._record_success(key)
                    if isinstance(result, dict):
                        result.setdefault("attempts", attempt)
                    return result
                except (CircuitOpenError, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    retryable = self._retryable(None, exc)
                    if attempt >= attempts or not retryable:
                        await self._record_failure(key)
                        raise
                    delay = min(
                        self.settings.http_retry_max_seconds,
                        self.settings.http_retry_base_seconds * (2 ** (attempt - 1)),
                    )
                    await asyncio.sleep(delay * random.uniform(0.75, 1.25))
        raise RuntimeError("provider guard exhausted without a result")


provider_guard = ProviderGuard()
