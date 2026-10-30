from pathlib import Path

from backend.app.evaluation.support_blind import CITATION_PATTERN, SupportBlindRunner


def test_support_blind_sets_have_80_unique_cases():
    for name in (
        "support_blind_v1.jsonl",
        "support_blind_v2.jsonl",
        "support_blind_v3.jsonl",
        "support_blind_v4.jsonl",
        "support_blind_v5.jsonl",
    ):
        runner = SupportBlindRunner(
            cases_path=Path("evals/support") / name,
            output_root=Path("/tmp/support-blind-tests"),
        )
        cases = runner.load_cases()

        assert len(cases) == 80
        assert len({case.id for case in cases}) == 80
        assert {
            block: sum(case.block == block for case in cases)
            for block in ("router", "rag", "fault", "safety", "e2e")
        } == {
            "router": 15,
            "rag": 20,
            "fault": 15,
            "safety": 10,
            "e2e": 20,
        }


def test_support_blind_router_metrics_helpers():
    assert SupportBlindRunner._macro_f1(["a", "a", "b"], ["a", "b", "b"]) == 66.7
    assert SupportBlindRunner._percentile([10, 20, 30, 40], 50) == 20
    assert SupportBlindRunner._percentile([10, 20, 30, 40], 95) == 40


def test_support_blind_citation_parser_uses_full_chunk_ids():
    text = "图片可能被压缩 [abcdef1234567890abcdef12]。另见 [bad-id]。"
    assert CITATION_PATTERN.findall(text) == ["abcdef1234567890abcdef12"]
