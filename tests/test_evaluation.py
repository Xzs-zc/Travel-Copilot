from pathlib import Path

from backend.app.evaluation.images import analyze_image, hamming_distance, is_black, load_image_bytes, unique_hashes
from backend.app.evaluation.models import EvaluationCaseResult, EvaluationReport
from backend.app.evaluation.report import write_report

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "images"


def test_image_metrics_and_uniqueness():
    data, mime = load_image_bytes(str(FIXTURES / "westlake_hero.jpg"))
    metrics = analyze_image(data)
    assert mime == "image/jpeg"
    assert metrics["width"] >= 1200
    assert metrics["height"] >= 700
    assert not is_black(metrics)
    assert hamming_distance(metrics["dhash"], metrics["dhash"]) == 0
    assert unique_hashes([metrics["dhash"], metrics["dhash"]]) == 1


def test_report_writer(tmp_path):
    report = EvaluationReport(
        run_id="test-run",
        suite="offline",
        started_at="2026-09-21T00:00:00+08:00",
        passed=True,
        metrics={"task_success_rate": 100},
        cases=[EvaluationCaseResult(
            id="case-1", name="测试", category="agent", suite="offline",
            hard_gate=True, passed=True, score=100,
        )],
    )
    json_path, md_path, html_path = write_report(report, tmp_path)
    assert json_path.exists() and md_path.exists() and html_path.exists()
    assert "Travel Copilot" in html_path.read_text(encoding="utf-8")


def test_minimal_agent_decision_suite(monkeypatch):
    from backend.app.evaluation import decision

    async def fake_assess(*args, **kwargs):
        return {"score": 10, "match": False, "reason": "test"}

    monkeypatch.setattr(decision, "assess_image_match", fake_assess)
    cases = decision.run_decision_cases()
    assert len(cases) == 6
    assert {case.id for case in cases} == {
        "decision-ticket-fallback",
        "decision-official-downgrade",
        "decision-date-delta",
        "decision-source-conflict",
        "decision-image-mismatch",
        "decision-forbidden-action",
    }
    assert all(case.passed for case in cases)


def test_extended_cases_are_independent_and_complete():
    from backend.app.evaluation.runner import EvaluationRunner

    runner = EvaluationRunner(base_url="http://127.0.0.1:1", run_dir=Path("/tmp/travel-eval-test"))
    cases = runner._extended_cases()
    assert len(cases) == 44
    assert len({case.id for case in cases}) == 44
    assert all(case.passed for case in cases)


def test_no_vision_ablation_uses_metadata_only():
    from backend.app.evaluation import decision

    cases = decision.run_decision_cases("no_vision")
    mismatch = next(case for case in cases if case.id == "decision-image-mismatch")
    assert not mismatch.passed
    assert mismatch.metrics["vision"]["model"] == "metadata"
