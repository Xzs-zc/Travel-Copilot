from __future__ import annotations

import contextlib
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any

from .config import get_settings

_client: Any | None = None
_initialized = False


@dataclass
class TraceHandle:
    observation: Any | None = None
    trace_id: str = ""
    span_id: str = ""
    context: dict[str, str] | None = None


def _client_or_none() -> Any | None:
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key.strip() or not settings.langfuse_secret_key.strip():
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_base_url or None,
            environment=settings.app_env,
        )
    except Exception:
        _client = None
    return _client


def trace_value(value: Any, *, max_string: int = 3000, max_items: int = 40, depth: int = 0) -> Any:
    if depth > 6:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "...<truncated>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["<truncated>"] = True
                break
            name = str(key)
            if name.lower() in {"api_key", "authorization", "secret", "secret_key", "password", "token"}:
                result[name] = "<redacted>"
            else:
                result[name] = trace_value(item, max_string=max_string, max_items=max_items, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [trace_value(item, max_string=max_string, max_items=max_items, depth=depth + 1) for item in items[:max_items]]
        if len(items) > max_items:
            result.append("<truncated>")
        return result
    if hasattr(value, "model_dump"):
        return trace_value(value.model_dump(mode="json"), max_string=max_string, max_items=max_items, depth=depth + 1)
    return trace_value(str(value), max_string=max_string, max_items=max_items, depth=depth + 1)


def usage_details(raw: dict[str, int] | None) -> dict[str, int]:
    values = raw or {}
    result = {
        "input": int(values.get("prompt_tokens") or values.get("input_tokens") or 0),
        "output": int(values.get("completion_tokens") or values.get("output_tokens") or 0),
        "total": int(values.get("total_tokens") or 0),
    }
    cached = values.get("prompt_cache_hit_tokens") or values.get("cache_read_input_tokens")
    if cached is not None:
        result["input_cached"] = int(cached)
    return result


def observation_context(item: Any | None) -> dict[str, str] | None:
    if item is None:
        return None
    trace_id = getattr(item, "trace_id", "")
    span_id = getattr(item, "id", "")
    if not trace_id or not span_id:
        return None
    return {"trace_id": trace_id, "parent_span_id": span_id}


def start_trace(
    *,
    name: str,
    input: Any = None,
    metadata: Any = None,
    user_id: str = "local-user",
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> TraceHandle:
    client = _client_or_none()
    if client is None:
        return TraceHandle()
    try:
        from langfuse import propagate_attributes

        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata=trace_value(metadata),
            tags=tags or [],
            trace_name=name,
        ):
            item = client.start_observation(name=name, as_type="span", input=trace_value(input), metadata=trace_value(metadata))
        return TraceHandle(observation=item, trace_id=item.trace_id, span_id=item.id, context=observation_context(item))
    except Exception:
        return TraceHandle()


def finish_trace(handle: TraceHandle, *, output: Any = None, metadata: Any = None) -> None:
    item = handle.observation
    if item is None:
        return
    try:
        item.update(output=trace_value(output), metadata=trace_value(metadata))
    except Exception:
        pass
    try:
        item.end()
    except Exception:
        pass


def start_observation(
    *,
    name: str,
    as_type: str = "span",
    input: Any = None,
    metadata: Any = None,
    model: str | None = None,
    trace_context: dict[str, str] | None = None,
) -> Any | None:
    client = _client_or_none()
    if client is None:
        return None
    try:
        return client.start_observation(
            trace_context=trace_context,
            name=name,
            as_type=as_type,
            input=trace_value(input),
            metadata=trace_value(metadata),
            model=model,
        )
    except Exception:
        return None


def finish_observation(item: Any | None, *, output: Any = None, metadata: Any = None) -> None:
    if item is None:
        return
    try:
        item.update(output=trace_value(output), metadata=trace_value(metadata))
    except Exception:
        pass
    try:
        item.end()
    except Exception:
        pass


def score_trace(handle: TraceHandle, name: str, value: float | str, *, data_type: str | None = None, comment: str = "") -> None:
    item = handle.observation
    if item is None:
        return
    try:
        item.score_trace(name=name, value=value, data_type=data_type, comment=comment or None)
    except Exception:
        pass


@contextlib.contextmanager
def observation(
    *,
    name: str,
    as_type: str = "span",
    input: Any = None,
    output: Any = None,
    metadata: Any = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
    trace_context: dict[str, str] | None = None,
) -> Iterator[Any | None]:
    client = _client_or_none()
    if client is None:
        yield None
        return
    with client.start_as_current_observation(
        as_type=as_type,
        name=name,
        input=trace_value(input),
        output=trace_value(output),
        metadata=trace_value(metadata),
        model=model,
        model_parameters=model_parameters,
        trace_context=trace_context,
    ) as item:
        yield item


def update(item: Any | None, **kwargs: Any) -> None:
    if item is None:
        return
    try:
        item.update(**{key: trace_value(value) for key, value in kwargs.items()})
    except Exception:
        pass


def flush() -> None:
    client = _client_or_none()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        pass
