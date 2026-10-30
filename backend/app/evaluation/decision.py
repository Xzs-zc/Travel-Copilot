from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from backend.app.agent.state import EvidenceItem, build_replan_delta, detect_evidence_conflicts, preferred_evidence
from backend.app.agent.validators import validate_arguments, validate_final_result, validate_tool_result
from backend.app.schemas import SourceRef

from .images import analyze_image, is_black, load_image_bytes
from .models import EvaluationCaseResult
from .vision import VisionUnavailable, assess_image_match

PROJECT_DIR = Path(__file__).resolve().parents[3]
IMAGE_DIR = PROJECT_DIR / "evals" / "fixtures" / "images"
TRAIN_CODE = re.compile(r"\b[GDCZKT]\d{1,4}\b", re.IGNORECASE)


def run_decision_cases(variant: str = "full") -> list[EvaluationCaseResult]:
    return [
        _ticket_fallback_case(),
        _official_downgrade_case(),
        _date_delta_case(),
        _source_conflict_case(),
        _image_mismatch_case(variant),
        _forbidden_action_case(),
    ]


def _ticket_fallback_case() -> EvaluationCaseResult:
    start = time.perf_counter()
    ticket = validate_tool_result("query_train", {"ok": False, "error": "12306 anti-bot"}, {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"})
    route = validate_tool_result(
        "route_estimate",
        {"ok": True, "route": {"origin_location": "121,31", "destination_location": "120,30", "distance_m": 177000, "source": {"title": "高德驾车路线", "provider": "高德地图"}}},
        {"origin": "上海", "destination": "杭州"},
    )
    final = validate_final_result("submit_transport_plan", {"blessing": "愿君此行，湖山相迎。", "caveats": ["12306 暂时不可用，已提供驾车方案"]})
    failures = [] if (not ticket.ok and route.ok and final.ok and not TRAIN_CODE.search(str(final))) else ["交通降级决策错误"]
    return _case("decision-ticket-fallback", "12306 失败后驾车降级", start, failures, {"ticket_error": ticket.errors, "route_ok": route.ok, "final_ok": final.ok}, ["decision_fallback_error"] if failures else [])


def _official_downgrade_case() -> EvaluationCaseResult:
    start = time.perf_counter()
    official = validate_tool_result(
        "verify_official_page",
        {"ok": True, "status": "amap_only", "url": "", "matches": []},
        {"name": "黄山风景区", "url": "", "keywords": ["开放时间", "票价"]},
    )
    final = validate_final_result("submit_attraction_page", {"summary": "官网暂不可用，开放信息和票价待确认。", "travel_tips": ["出行前请再次核验官网。"], "open_time": "", "ticket_price": ""})
    passed = "official_information_unverified" in official.warnings and final.ok and not final.errors
    failures = [] if passed else ["官网不可用时错误补造或未降级"]
    return _case("decision-official-downgrade", "官网不可用时待确认降级", start, failures, {"warning": official.warnings, "final_ok": final.ok}, ["decision_official_downgrade"] if failures else [])


def _date_delta_case() -> EvaluationCaseResult:
    start = time.perf_counter()
    delta = build_replan_delta(
        "transport_plan",
        {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01", "days": 3},
        {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-02", "days": 3},
    )
    passed = "transport.route" in delta.reused_evidence and {"transport.ticket_periods", "weather.forecast"}.issubset(set(delta.invalidated_evidence))
    failures = [] if passed else ["日期变化没有执行最小重算"]
    return _case("decision-date-delta", "日期变化只重算受影响数据", start, failures, delta.model_dump(mode="json"), ["decision_delta_scope"] if failures else [])


def _source_conflict_case() -> EvaluationCaseResult:
    start = time.perf_counter()
    items = [
        EvidenceItem(id="official.open", field="attraction.open_time", value="08:00-17:00", source=SourceRef(title="官网", provider="官方网站")),
        EvidenceItem(id="amap.open", field="attraction.open_time", value="24小时营业", source=SourceRef(title="高德", provider="高德地图")),
    ]
    conflicts = detect_evidence_conflicts(items)
    preferred = preferred_evidence(items, "attraction.open_time")
    passed = len(conflicts) == 2 and all(item.status == "conflict" for item in conflicts) and preferred is not None and preferred.source.provider == "官方网站"
    failures = [] if passed else ["来源冲突未检测或未优先官网"]
    return _case("decision-source-conflict", "来源冲突优先官网证据", start, failures, {"conflicts": [item.id for item in conflicts], "preferred": preferred.id if preferred else ""}, ["decision_conflict_resolution"] if failures else [])


def _image_mismatch_case(variant: str) -> EvaluationCaseResult:
    start = time.perf_counter()
    failures: list[str] = []
    metrics: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    try:
        data, mime = load_image_bytes(str(IMAGE_DIR / "westlake_hero.jpg"))
        if variant == "no_vision":
            analyzed = analyze_image(data)
            accepted = not is_black(analyzed) and analyzed["width"] >= 1200 and analyzed["height"] >= 700
            vision = {
                "score": 80 if accepted else 0,
                "match": accepted,
                "reason": "no_vision_metadata_rule",
                "model": "metadata",
            }
        else:
            vision = asyncio.run(assess_image_match("故宫博物院", "北京", data, mime))
        metrics = {"vision": vision}
        trace.append({"event": "vision_assessment", **vision})
        if vision["match"]:
            failures.append("错误景区图片被接受")
    except VisionUnavailable as exc:
        failures.append(str(exc))
    except Exception as exc:
        failures.append(str(exc))
    return _case("decision-image-mismatch", "图片不匹配时拒绝使用", start, failures, metrics, ["decision_image_false_accept"] if failures else [], trace)


def _forbidden_action_case() -> EvaluationCaseResult:
    start = time.perf_counter()
    normalized, errors = validate_arguments("submit_payment", {"amount": 100})
    passed = normalized is None and bool(errors)
    failures = [] if passed else ["付款工具没有被白名单阻断"]
    return _case("decision-forbidden-action", "付款与预订动作阻断", start, failures, {"errors": errors}, ["decision_forbidden_action"] if failures else [])


def _case(case_id: str, name: str, start: float, failures: list[str], metrics: dict[str, Any], root_causes: list[str], trace: list[dict[str, Any]] | None = None) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        id=case_id,
        name=name,
        category="decision",
        suite="offline",
        hard_gate=True,
        passed=not failures,
        score=max(0, 100 - len(failures) * 30),
        duration_ms=int((time.perf_counter() - start) * 1000),
        metrics=metrics,
        failures=failures,
        root_causes=root_causes,
        trace=trace or [],
    )
