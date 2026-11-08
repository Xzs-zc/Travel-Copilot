from __future__ import annotations

import sys
from pathlib import Path

import pytest


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import db  # noqa: E402
from platform_backend.judge.calibration import cohen_kappa, weighted_kappa  # noqa: E402
from platform_backend.judge.service import JudgeService  # noqa: E402


@pytest.mark.asyncio
async def test_judge_cache_and_parse_failure_are_explicit() -> None:
    db.init_db()
    calls = 0

    async def valid_completion(prompt: str) -> dict:
        nonlocal calls
        calls += 1
        return {
            "choices": [{"message": {"content": '{"score": 5, "reason": "supported"}'}}],
            "usage": {"total_tokens": 12},
        }

    service = JudgeService(model="stub-valid", completion=valid_completion)
    sample = {"question": "q-valid", "context": ["c"]}
    output = {"answer": "a-valid"}
    first = await service.call(
        rubric_name="faithfulness",
        sample=sample,
        output=output,
    )
    second = await service.call(
        rubric_name="faithfulness",
        sample=sample,
        output=output,
    )
    try:
        assert first.status == "ok"
        assert first.result["score"] == 5
        assert second.cached is True
        assert calls == 1
    finally:
        with db.connection() as conn:
            conn.execute("DELETE FROM judgments WHERE judge_model = 'stub-valid'")

    failing_calls = 0

    async def invalid_completion(prompt: str) -> dict:
        nonlocal failing_calls
        failing_calls += 1
        return {"choices": [{"message": {"content": "not-json"}}]}

    failing = JudgeService(model="stub-invalid", completion=invalid_completion)
    result = await failing.call(
        rubric_name="answer_relevance",
        sample={"question": "q-invalid"},
        output={"answer": "a-invalid"},
    )
    try:
        assert result.status == "judge_error"
        assert failing_calls == 2
        assert result.result["error"]
        retry_service = JudgeService(model="stub-invalid", completion=valid_completion)
        retried = await retry_service.call(
            rubric_name="answer_relevance",
            sample={"question": "q-invalid"},
            output={"answer": "a-invalid"},
        )
        assert retried.status == "ok"
        assert retried.result["score"] == 5
    finally:
        with db.connection() as conn:
            conn.execute("DELETE FROM judgments WHERE judge_model = 'stub-invalid'")


def test_kappa_known_small_samples() -> None:
    assert cohen_kappa([0, 0, 1, 1], [0, 1, 0, 1]) == 0.0
    assert cohen_kappa([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert weighted_kappa([1, 2, 3, 1], [1, 2, 3, 1]) == 1.0
