import json

import pytest

from backend.app.evaluation.rag_quality import (
    RAGSelfImprovementLoop,
    RSIConfig,
    _extract_json_object,
    _is_complex_question,
    _normalize_judge,
)


def test_extract_json_object_handles_fenced_content():
    payload = _extract_json_object(
        '```json\n{"faithfulness":{"score":90},"reason":"ok"}\n```'
    )
    assert payload["faithfulness"]["score"] == 90


def test_normalize_judge_clamps_scores():
    payload = _normalize_judge(
        {
            "faithfulness": {"score": 130},
            "citation_accuracy": {"score": -2},
            "answer_relevance": {"score": "87"},
            "reason": "test",
        }
    )
    assert payload["faithfulness"]["score"] == 100
    assert payload["citation_accuracy"]["score"] == 0
    assert payload["answer_relevance"]["score"] == 87


def test_normalize_judge_accepts_malformed_score_keys():
    payload = _normalize_judge(
        {
            "faithfulness": {"score": 95},
            "citation_accuracy": {"score": 94},
            "answer_relevance": {"score": 0, "score90,": 0},
        }
    )
    assert payload["answer_relevance"]["score"] == 90


def test_extract_json_object_rejects_missing_json():
    with pytest.raises(ValueError):
        _extract_json_object("not json")


def test_complex_question_detection():
    assert _is_complex_question("景区图片既模糊又重复时，应该从哪些方面排查？")
    assert not _is_complex_question("POI 是什么？")


async def test_rsi_loop_respects_planning_and_judge_limits():
    retrieval_calls = []

    async def retrieve(query, routes):
        retrieval_calls.append(query)
        return {
            "documents": [
                {"id": "a" * 24, "doc_id": "faq", "section": "A", "content": "A"},
                {"id": "b" * 24, "doc_id": "guide", "section": "B", "content": "B"},
            ],
            "retrieval": "hybrid",
            "error": "",
        }

    async def plan_queries(_question):
        return ["子查询一", "子查询二", "子查询三"]

    async def generate_answer(_question, documents):
        return f"回答[{documents[0]['id']}]", {"total_tokens": 10}

    async def revise_answer(_question, _answer, documents, _feedback):
        return f"修订[{documents[0]['id']}]", {"total_tokens": 10}

    async def judge_answer(_question, _answer, _documents):
        return {
            "faithfulness": {"score": 80},
            "citation_accuracy": {"score": 80},
            "answer_relevance": {"score": 80},
            "reason": "coverage low",
        }, {"total_tokens": 10}

    loop = RAGSelfImprovementLoop(
        config=RSIConfig(
            max_planning_rounds=2,
            max_total_answer_attempts=3,
            max_judge_calls=3,
            max_queries=2,
            time_budget_seconds=30,
        ),
        retrieve=retrieve,
        plan_queries=plan_queries,
        generate_answer=generate_answer,
        revise_answer=revise_answer,
        judge_answer=judge_answer,
    )
    result = await loop.run("景区图片既模糊又重复怎么办？", ["troubleshooter"])
    assert len(retrieval_calls) == 2
    assert result["planning_rounds"] <= 2
    assert result["judge_calls"] <= 3
    assert "stop_no_improvement" in result["actions"]
