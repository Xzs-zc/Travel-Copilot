from __future__ import annotations

import os
import json
import sys
from pathlib import Path

import pytest


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import cli  # noqa: E402
from platform_backend.adapters import support_online  # noqa: E402
from platform_backend.core.system_config import BUILTIN_SYSTEM_CONFIGS  # noqa: E402
from platform_backend.core.task import execute_task  # noqa: E402
from platform_backend.core.tracing import trace_session  # noqa: E402
from platform_backend.graders import grade_case  # noqa: E402


class StubExecutor:
    def __init__(self, actual: dict) -> None:
        self.actual = actual

    async def execute(self, _input_data: dict) -> dict:
        return self.actual


@pytest.mark.asyncio
async def test_support_e2e_task_and_grader(monkeypatch) -> None:
    actual = {
        "status": "passed",
        "duration_ms": 10,
        "route": {"primary_agent": "site_guide"},
        "tool_calls": [],
        "reply": "在呢，可以帮你查页面信息。",
        "error": "",
        "token_usage": {"total_tokens": 10},
        "event_timings": [
            {"event": "status", "offset_ms": 2, "data": {"phase": "supervisor"}},
            {"event": "done", "offset_ms": 8, "data": {"reply": "在呢"}},
        ],
        "timing_scope": "consumer_arrival",
    }
    monkeypatch.setattr(support_online, "_EXECUTOR", StubExecutor(actual))
    case = {
        "case_key": "ONLINE-1",
        "name": "online",
        "category": "online_e2e",
        "severity": "high",
        "difficulty": "medium",
        "input": {
            "type": "support_e2e",
            "message": "你好",
            "context": {"route": "/", "page_type": "home"},
            "expected": {},
        },
        "expected": {
            "primary_agent": "site_guide",
            "forbidden_tools": ["pay_order"],
            "forbidden_reply_patterns": [],
            "required_facts": ["页面信息"],
        },
    }
    with trace_session("online-test") as tracer:
        output = await execute_task(
            case,
            BUILTIN_SYSTEM_CONFIGS["candidate-current"],
        )
    grade = grade_case(case, output.actual)
    assert output.actual["timing_scope"] == "consumer_arrival"
    assert grade.passed is True
    assert any(
        item["name"] == "support.stream_reply" for item in tracer.records()
    )
    assert any(
        item["name"] == "support.event.done" for item in tracer.records()
    )


def test_online_dataset_preserves_messages_and_page_context() -> None:
    path = PLATFORM_ROOT / "datasets" / "online_support_e2e_v1.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 20
    assert all(row["input"]["message"] for row in rows)
    assert all("context" in row["input"] for row in rows)
    assert {row["split"] for row in rows} == {"all"}


def test_paid_guard_blocks_without_allow_paid(monkeypatch, capsys) -> None:
    monkeypatch.delenv("EVAL_ALLOW_PAID", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evalctl",
            "run",
            "--dataset",
            "online-support-e2e-v1",
            "--system",
            "candidate-current",
            "--trials",
            "1",
        ],
    )
    assert cli.main() == 2
    assert "paid_guard" in capsys.readouterr().out
