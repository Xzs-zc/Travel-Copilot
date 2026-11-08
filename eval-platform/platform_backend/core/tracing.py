from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    span_type: str
    status: str
    start_ms: float
    duration_ms: float
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error_text: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "span_type": self.span_type,
            "status": self.status,
            "start_ms": round(self.start_ms, 3),
            "duration_ms": round(self.duration_ms, 3),
            "input": self.input,
            "output": self.output,
            "metadata": self.metadata,
            "error_text": self.error_text,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "model": self.model,
        }


@dataclass
class SpanHandle:
    record: SpanRecord | None = None

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        error_text: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        model: str | None = None,
    ) -> None:
        if self.record is None:
            return
        if output is not None:
            self.record.output = output
        if metadata:
            self.record.metadata.update(metadata)
        if status is not None:
            self.record.status = status
        if error_text is not None:
            self.record.error_text = error_text
        if tokens_in is not None:
            self.record.tokens_in = tokens_in
        if tokens_out is not None:
            self.record.tokens_out = tokens_out
        if cost_usd is not None:
            self.record.cost_usd = cost_usd
        if model is not None:
            self.record.model = model


@dataclass
class TraceSession:
    name: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_perf: float = field(default_factory=time.perf_counter)
    spans: list[SpanRecord] = field(default_factory=list)
    stack: list[str] = field(default_factory=list)

    def records(self) -> list[dict[str, Any]]:
        ordered = sorted(self.spans, key=lambda item: (item.start_ms, item.name))
        return [item.as_dict() for item in ordered]


_CURRENT_TRACE: ContextVar[TraceSession | None] = ContextVar("eval_trace", default=None)


@contextmanager
def trace_session(name: str) -> Iterator[TraceSession]:
    session = TraceSession(name=name)
    token = _CURRENT_TRACE.set(session)
    try:
        yield session
    finally:
        _CURRENT_TRACE.reset(token)


@contextmanager
def span(
    name: str,
    span_type: str,
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[SpanHandle]:
    session = _CURRENT_TRACE.get()
    if session is None:
        yield SpanHandle()
        return
    started = time.perf_counter()
    parent_span_id = session.stack[-1] if session.stack else None
    record = SpanRecord(
        trace_id=session.trace_id,
        span_id=uuid.uuid4().hex[:16],
        parent_span_id=parent_span_id,
        name=name,
        span_type=span_type,
        status="passed",
        start_ms=max(0.0, (started - session.started_perf) * 1000),
        duration_ms=0.0,
        input=input,
        metadata=metadata or {},
    )
    session.spans.append(record)
    session.stack.append(record.span_id)
    handle = SpanHandle(record)
    try:
        yield handle
    except Exception as exc:
        record.status = "error"
        record.error_text = str(exc)[:2000]
        raise
    finally:
        record.duration_ms = max(0.0, (time.perf_counter() - started) * 1000)
        session.stack.pop()


def record_span(
    name: str,
    span_type: str,
    *,
    start_ms: float,
    duration_ms: float,
    input: Any = None,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    status: str = "passed",
    error_text: str = "",
) -> None:
    session = _CURRENT_TRACE.get()
    if session is None:
        return
    session.spans.append(
        SpanRecord(
            trace_id=session.trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=session.stack[-1] if session.stack else None,
            name=name,
            span_type=span_type,
            status=status,
            start_ms=max(0.0, start_ms),
            duration_ms=max(0.0, duration_ms),
            input=input,
            output=output,
            metadata=metadata or {},
            error_text=error_text[:2000],
        )
    )
