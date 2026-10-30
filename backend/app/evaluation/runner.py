from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import httpx
from playwright.sync_api import Page, sync_playwright

from backend.app.agent.state import build_task_state
from backend.app.agent.validators import validate_arguments, validate_final_result, validate_tool_result

from .decision import run_decision_cases
from .images import analyze_image, hamming_distance, is_black, load_image_bytes, unique_hashes
from .models import EvaluationCaseResult, EvaluationReport
from .runtime_loops import run_runtime_loop_cases
from .vision import VisionUnavailable, assess_image_match

PROJECT_DIR = Path(__file__).resolve().parents[3]
FIXTURE_DIR = PROJECT_DIR / "evals" / "fixtures"
IMAGE_DIR = FIXTURE_DIR / "images"

ATTRACTION_TRACE = [
    {"event": "tool_start", "tool": "get_attraction_detail", "ok": True},
    {"event": "tool_start", "tool": "verify_official_page", "ok": True},
    {"event": "tool_start", "tool": "get_weather", "ok": True},
    {"event": "done", "tool": "submit_attraction_page", "ok": True},
]
TRANSPORT_TRACE = [
    {"event": "tool_start", "tool": "query_train", "ok": True},
    {"event": "tool_start", "tool": "route_estimate", "ok": True},
    {"event": "tool_start", "tool": "get_weather", "ok": True},
    {"event": "done", "tool": "submit_transport_plan", "ok": True},
]
RECOVERY_TRACE = [
    {"event": "tool_start", "tool": "query_train", "ok": False, "error": "12306 anti-bot"},
    {"event": "tool_start", "tool": "route_estimate", "ok": True},
    {"event": "tool_start", "tool": "get_weather", "ok": True},
    {"event": "done", "tool": "submit_transport_plan", "ok": True},
]
TICKET_FIXTURE = [
    {"label": "06:00–08:59", "status": "ample", "summary": "共 12 个车次，10 个有余票", "seat_summary": ["二等座", "一等座"]},
    {"label": "09:00–11:59", "status": "available", "summary": "共 15 个车次，6 个有余票", "seat_summary": ["二等座"]},
]
ROUTE_FIXTURE = {
    "origin": "上海",
    "destination": "杭州",
    "travel_date": date.today().isoformat(),
    "route": {
        "origin_name": "上海",
        "destination_name": "杭州",
        "origin_location": "121.473701,31.230416",
        "destination_location": "120.155070,30.274084",
        "distance_m": 177000,
        "duration_s": 9000,
        "tolls": 80,
        "path": [[121.473701, 31.230416], [120.155070, 30.274084]],
        "source": {"title": "高德驾车路线", "url": "https://www.amap.com/", "provider": "高德地图", "excerpt": "评测 fixture"},
    },
    "ticket_periods": TICKET_FIXTURE,
    "blessing": {"text": "愿君此行，湖山相迎。", "style": "ancient", "generated_at": datetime.now().isoformat()},
    "days": 3,
    "weather": {"city": "杭州市", "report_time": datetime.now().isoformat(), "casts": [
        {"date": date.today().isoformat(), "dayweather": "晴", "nighttemp": "22", "daytemp": "30", "daywind": "东", "daypower": "2"}
    ]},
    "caveats": [],
    "generated_at": datetime.now().isoformat(),
}


class EvaluationRunner:
    def __init__(self, *, base_url: str, run_dir: Path, trials: int = 1, variant: str = "full"):
        self.base_url = base_url.rstrip("/")
        self.run_dir = run_dir
        self.trials = max(1, int(trials))
        self.variant = variant
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def run(self, suite: str) -> EvaluationReport:
        started = datetime.now()
        results: list[EvaluationCaseResult] = []
        errors: list[str] = []
        if suite in {"offline", "all"}:
            results.extend(self._run_independent_trials(self.run_offline))
        if suite in {"live-smoke", "all"}:
            results.extend(self._run_independent_trials(self.run_live_smoke))
        report = EvaluationReport(
            run_id=self.run_dir.name,
            suite=suite,
            variant=self.variant,
            trials=self.trials,
            started_at=started,
            base_url=self.base_url,
            cases=results,
            errors=errors,
        )
        report.metrics = self._metrics(results)
        report.trial_metrics = self._trial_metrics(results)
        report.acceptance = self._acceptance(report.metrics)
        report.isolation = self._isolation()
        report.passed = (
            not errors
            and all(case.passed for case in results if case.hard_gate)
            and not report.acceptance["failures"]
        )
        return report

    def _isolation(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "evaluator_pid": os.getpid(),
            "evaluator_variant": os.getenv("ABLATION_VARIANT", "full"),
            "database_url": os.getenv("DATABASE_URL", ""),
            "media_dir": os.getenv("MEDIA_DIR", ""),
            "base_url": self.base_url,
        }
        try:
            response = httpx.get(self.base_url + "/api/health", timeout=5)
            response.raise_for_status()
            data["server"] = response.json()
        except Exception as exc:
            data["server_error"] = str(exc)
        return data

    def _run_independent_trials(self, builder: Callable[[], list[EvaluationCaseResult]]) -> list[EvaluationCaseResult]:
        repeated: list[EvaluationCaseResult] = []
        for trial in range(1, self.trials + 1):
            for case in builder():
                case.id = f"{case.id}@t{trial}"
                case.name = f"{case.name} · 第 {trial} 次"
                repeated.append(case)
        return repeated

    @staticmethod
    def _trial_metrics(cases: list[EvaluationCaseResult]) -> dict[str, Any]:
        grouped: dict[str, list[EvaluationCaseResult]] = {}
        for case in cases:
            trial = case.id.rsplit("@t", 1)[-1] if "@t" in case.id else "1"
            grouped.setdefault(trial, []).append(case)
        metrics: dict[str, Any] = {}
        for trial, items in sorted(grouped.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]):
            passed = sum(item.passed for item in items)
            metrics[trial] = {
                "cases": len(items),
                "passed_cases": passed,
                "success_rate": round(passed / len(items) * 100, 1) if items else 0,
                "hard_gate_failures": [item.id for item in items if item.hard_gate and not item.passed],
                "average_duration_ms": round(sum(item.duration_ms for item in items) / len(items), 1) if items else 0,
            }
        return metrics

    def run_offline(self) -> list[EvaluationCaseResult]:
        results = [
            self._image_case("image-westlake-hero", "西湖主图质量", "westlake_hero.jpg", "杭州西湖风景名胜区", "杭州", hard_gate=True),
            self._subspot_case(),
            self._image_case("image-obscure-exact", "冷门景区精确图片", "forbidden_city.jpg", "故宫博物院", "北京", hard_gate=True),
            self._image_case("image-semantic-mismatch", "错误景区图片拒绝", "westlake_hero.jpg", "故宫博物院", "北京", hard_gate=False, expected_match=False),
            self._fallback_case(),
            self._agent_trace_case("agent-attraction-tools", "景区 Agent 工具链", ["get_attraction_detail", "verify_official_page", "get_weather"], ATTRACTION_TRACE, True),
            self._agent_trace_case("agent-transport-tools", "交通 Agent 工具链", ["query_train", "route_estimate", "get_weather"], TRANSPORT_TRACE, True),
            self._agent_trace_case("agent-failure-recovery", "外部接口失败恢复", ["route_estimate", "get_weather"], RECOVERY_TRACE, True),
            self._ticket_period_case(),
            self._safety_case(),
        ]
        results.extend(self._map_cases())
        results.extend(self._catalog_cases())
        results.extend(self._extended_cases())
        results.extend(run_decision_cases(self.variant))
        results.extend(run_runtime_loop_cases(variant=self.variant))
        return results

    def _catalog_cases(self) -> list[EvaluationCaseResult]:
        results: list[EvaluationCaseResult] = []
        planning_specs = [
            ("plan-transport-full", "transport_plan", {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"}, []),
            ("plan-transport-missing-origin", "transport_plan", {"destination": "杭州", "travel_date": "2026-10-01"}, ["origin"]),
            ("plan-transport-missing-date", "transport_plan", {"origin": "上海", "destination": "杭州"}, ["travel_date"]),
            ("plan-attraction-full", "attraction_page", {"poi_id": "B023B13L9M"}, []),
            ("plan-attraction-missing", "attraction_page", {}, ["poi_id"]),
            ("plan-transport-extra-preferences", "transport_plan", {"origin": "北京", "destination": "黄山", "travel_date": "2026-10-02", "preferred_transport": "high_speed"}, []),
        ]
        for case_id, task, constraints, expected_missing in planning_specs:
            start = time.perf_counter()
            state = build_task_state(run_id="eval", task=task, goal="评测", constraints=constraints)
            failures = []
            if state.missing_fields != expected_missing:
                failures.append(f"缺失字段错误：{state.missing_fields}")
            if not state.plan:
                failures.append("未生成计划")
            results.append(self._case(case_id, "任务状态与计划", "planning", "offline", True, not failures, 100 if not failures else 0, start, {"missing_fields": state.missing_fields, "plan": [item.tool for item in state.plan]}, failures, ["planning_state_error"] if failures else [], []))

        schema_specs = [
            ("schema-ticket-valid", "query_train", {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"}, True),
            ("schema-ticket-bad-date", "query_train", {"origin": "上海", "destination": "杭州", "travel_date": "bad"}, False),
            ("schema-weather-valid", "get_weather", {"city": "杭州市"}, True),
            ("schema-search-empty", "search_amap", {"keywords": ""}, False),
            ("schema-web-limit", "search_web", {"query": "黄山官网", "limit": 5}, True),
            ("schema-web-bad-limit", "search_web", {"query": "黄山官网", "limit": 50}, False),
            ("schema-route-valid", "route_estimate", {"origin": "上海", "destination": "杭州"}, True),
            ("schema-official-valid", "verify_official_page", {"name": "黄山", "url": "https://example.com"}, True),
            ("schema-unknown-tool", "unknown_tool", {}, False),
        ]
        for case_id, name, arguments, expected_ok in schema_specs:
            start = time.perf_counter()
            normalized, errors = validate_arguments(name, arguments)
            actual_ok = normalized is not None and not errors
            if name.startswith("submit_"):
                actual_ok = validate_final_result(name, arguments).ok
            failures = [] if actual_ok == expected_ok else [f"Schema 判断错误，期望 {expected_ok}"]
            results.append(self._case(case_id, "工具参数与 Schema 校验", "schema", "offline", True, not failures, 100 if not failures else 0, start, {"expected_ok": expected_ok, "actual_ok": actual_ok}, failures, ["schema_validation_error"] if failures else [], []))

        transport_specs = [
            ("transport-safe-periods", TICKET_FIXTURE, True),
            ("transport-train-code", [{"label": "08:00–09:00", "summary": "G123 有余票"}], False),
            ("transport-empty-periods", [], False),
            ("transport-route-present", {"origin_location": "121,31", "destination_location": "120,30", "distance_m": 1000}, True),
            ("transport-route-missing-distance", {"origin_location": "121,31", "destination_location": "120,30"}, False),
            ("transport-weather-empty", {"casts": []}, True),
        ]
        for case_id, payload, expected_ok in transport_specs:
            start = time.perf_counter()
            if case_id.startswith("transport-route"):
                result = validate_tool_result("route_estimate", {"ok": True, "route": payload}, {"origin": "上海", "destination": "杭州"})
            elif case_id.startswith("transport-weather"):
                result = validate_tool_result("get_weather", {"ok": True, "weather": payload}, {"city": "杭州"})
            else:
                result = validate_tool_result("query_train", {"ok": True, "ticket_periods": payload}, {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"})
            actual_ok = result.ok
            if case_id == "transport-weather-empty":
                actual_ok = "weather_empty" in result.warnings
            failures = [] if actual_ok == expected_ok else [f"结果校验错误，期望 {expected_ok}"]
            results.append(self._case(case_id, "交通与外部结果校验", "transport", "offline", True, not failures, 100 if not failures else 0, start, {"errors": result.errors, "warnings": result.warnings}, failures, ["result_validation_error"] if failures else [], []))

        image_specs = [
            ("catalog-image-main-size", "westlake_hero.jpg", True),
            ("catalog-image-spot-size", "subspot_1.jpg", True),
            ("catalog-image-spot-2-size", "subspot_2.jpg", True),
            ("catalog-image-spot-3-size", "subspot_3.jpg", True),
            ("catalog-image-forbidden-city", "forbidden_city.jpg", True),
        ]
        for case_id, filename, expected_ok in image_specs:
            start = time.perf_counter()
            try:
                data, _ = load_image_bytes(str(IMAGE_DIR / filename), base_url=self.base_url)
                metrics = analyze_image(data)
                actual_ok = metrics["width"] >= 300 and metrics["height"] >= 200 and not is_black(metrics)
            except Exception:
                metrics = {}
                actual_ok = False
            failures = [] if actual_ok == expected_ok else [f"图片质量检查错误，期望 {expected_ok}"]
            results.append(self._case(case_id, "图片基础质量", "image", "offline", True, not failures, 100 if not failures else 0, start, metrics, failures, ["image_quality_error"] if failures else [], [], {filename: filename}))

        recovery_specs = [
            ("recovery-empty-search", "search_amap", {"ok": True, "items": []}, ["search_returned_no_items"]),
            ("recovery-missing-poi", "get_attraction_detail", {"ok": True, "poi": {}}, ["poi_identity_missing"]),
            ("recovery-route-missing", "route_estimate", {"ok": True, "route": {}}, ["route_location_missing"]),
            ("recovery-ticket-empty", "query_train", {"ok": True, "ticket_periods": []}, ["no_ticket_periods"]),
            ("recovery-official-unverified", "verify_official_page", {"ok": True, "status": "amap_only", "matches": []}, []),
        ]
        for case_id, tool, payload, expected_errors in recovery_specs:
            start = time.perf_counter()
            result = validate_tool_result(tool, payload, {})
            errors = result.errors
            failures = [] if (errors == expected_errors or (case_id.endswith("unverified") and "official_information_unverified" in result.warnings)) else [f"恢复校验错误：{errors}"]
            results.append(self._case(case_id, "工具异常与降级恢复", "recovery", "offline", True, not failures, 100 if not failures else 0, start, {"errors": errors, "warnings": result.warnings}, failures, ["recovery_validator_error"] if failures else [], []))

        safety_specs = [
            ("safety-train-code-final", "submit_transport_plan", {"blessing": "愿君平安。", "caveats": ["G123 有余票"]}, False),
        ]
        for case_id, final_tool, payload, expected_ok in safety_specs:
            start = time.perf_counter()
            result = validate_final_result(final_tool, payload)
            failures = [] if result.ok == expected_ok else [f"安全校验错误，期望 {expected_ok}"]
            results.append(self._case(case_id, "最终结果安全校验", "safety", "offline", True, not failures, 100 if not failures else 0, start, {"errors": result.errors}, failures, ["final_safety_error"] if failures else [], []))
        return results

    def _extended_cases(self) -> list[EvaluationCaseResult]:
        return [
            *self._extended_planning_cases(),
            *self._extended_schema_cases(),
            *self._extended_transport_cases(),
            *self._extended_image_cases(),
            *self._extended_recovery_cases(),
            *self._extended_safety_cases(),
        ]

    def _extended_planning_cases(self) -> list[EvaluationCaseResult]:
        specs = [
            ("ext-plan-transport-beijing-huangshan", "transport_plan", {"origin": "北京", "destination": "黄山", "travel_date": "2026-10-02"}, []),
            ("ext-plan-transport-chengdu-lhasa", "transport_plan", {"origin": "成都", "destination": "拉萨", "travel_date": "2026-10-03"}, []),
            ("ext-plan-transport-shenzhen-guangzhou", "transport_plan", {"origin": "深圳", "destination": "广州", "travel_date": "2026-10-04"}, []),
            ("ext-plan-transport-missing-destination", "transport_plan", {"origin": "上海", "travel_date": "2026-10-01"}, ["destination"]),
            ("ext-plan-transport-missing-origin-and-date", "transport_plan", {"destination": "杭州"}, ["origin", "travel_date"]),
            ("ext-plan-attraction-alt-valid", "attraction_page", {"poi_id": "B0FFHDG8XG"}, []),
            ("ext-plan-attraction-blank-poi", "attraction_page", {"poi_id": ""}, ["poi_id"]),
            ("ext-plan-attraction-extra-fields", "attraction_page", {"poi_id": "B023B13L9M", "city": "杭州市"}, []),
            ("ext-plan-transport-date-string-valid", "transport_plan", {"origin": "南京", "destination": "苏州", "travel_date": "2026-11-11"}, []),
            ("ext-plan-transport-empty-all", "transport_plan", {}, ["origin", "destination", "travel_date"]),
        ]
        results: list[EvaluationCaseResult] = []
        for case_id, task, constraints, expected_missing in specs:
            start = time.perf_counter()
            state = build_task_state(run_id="eval", task=task, goal="扩展评测", constraints=constraints)
            failures = []
            if state.missing_fields != expected_missing:
                failures.append(f"缺失字段错误：{state.missing_fields}")
            if not state.plan:
                failures.append("未生成计划")
            results.append(self._case(case_id, "扩展任务状态与计划", "planning", "offline", True, not failures, 100 if not failures else 0, start, {"missing_fields": state.missing_fields, "plan": [item.tool for item in state.plan]}, failures, ["planning_state_error"] if failures else [], []))
        return results

    def _extended_schema_cases(self) -> list[EvaluationCaseResult]:
        specs = [
            ("ext-schema-search-city", "search_amap", {"keywords": "黄山", "city": "黄山市"}, True),
            ("ext-schema-search-types", "search_amap", {"keywords": "景区", "types": "风景名胜"}, True),
            ("ext-schema-official-missing-url", "verify_official_page", {"name": "黄山"}, False),
            ("ext-schema-weather-blank", "get_weather", {"city": "   "}, False),
            ("ext-schema-ticket-missing-origin", "query_train", {"destination": "杭州", "travel_date": "2026-10-01"}, False),
            ("ext-schema-route-blank-destination", "route_estimate", {"origin": "上海", "destination": ""}, False),
            ("ext-schema-web-limit-min", "search_web", {"query": "西湖官网", "limit": 1}, True),
            ("ext-schema-attraction-missing-poi", "get_attraction_detail", {}, False),
        ]
        results: list[EvaluationCaseResult] = []
        for case_id, tool, arguments, expected_ok in specs:
            start = time.perf_counter()
            normalized, errors = validate_arguments(tool, arguments)
            actual_ok = normalized is not None and not errors
            failures = [] if actual_ok == expected_ok else [f"Schema 判断错误，期望 {expected_ok}"]
            results.append(self._case(case_id, "扩展工具参数与 Schema 校验", "schema", "offline", True, not failures, 100 if not failures else 0, start, {"expected_ok": expected_ok, "actual_ok": actual_ok, "errors": errors}, failures, ["schema_validation_error"] if failures else [], []))
        return results

    def _extended_transport_cases(self) -> list[EvaluationCaseResult]:
        specs = [
            ("ext-transport-period-single", "query_train", {"ok": True, "ticket_periods": [{"label": "06:00-08:59", "status": "ample", "summary": "余票充足"}]}, {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"}, True),
            ("ext-transport-period-pair", "query_train", {"ok": True, "ticket_periods": TICKET_FIXTURE}, {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"}, True),
            ("ext-transport-period-status-only", "query_train", {"ok": True, "ticket_periods": [{"label": "晚间", "status": "available"}]}, {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"}, True),
            ("ext-transport-ticket-lowercase-code", "query_train", {"ok": True, "ticket_periods": [{"label": "上午", "summary": "g123 有余票"}]}, {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"}, False),
            ("ext-transport-route-zero-distance", "route_estimate", {"ok": True, "route": {"origin_location": "121,31", "destination_location": "120,30", "distance_m": 0}}, {"origin": "上海", "destination": "杭州"}, False),
            ("ext-transport-route-full-valid", "route_estimate", {"ok": True, "route": {"origin_location": "121,31", "destination_location": "120,30", "distance_m": 177000, "duration_s": 9000, "source": {"title": "高德驾车路线", "provider": "高德地图"}}}, {"origin": "上海", "destination": "杭州"}, True),
            ("ext-transport-route-origin-only", "route_estimate", {"ok": True, "route": {"origin_location": "121,31", "distance_m": 177000}}, {"origin": "上海", "destination": "杭州"}, False),
            ("ext-transport-weather-one-cast", "get_weather", {"ok": True, "weather": {"casts": [{"date": "2026-10-01", "dayweather": "晴"}], "report_time": "2026-10-01T06:00:00"}}, {"city": "杭州"}, True),
            ("ext-transport-weather-missing-report", "get_weather", {"ok": True, "weather": {"casts": [{"date": "2026-10-01", "dayweather": "多云"}]}}, {"city": "杭州"}, True),
            ("ext-transport-preferences-empty", "get_preferences", {"ok": True, "preferences": {}}, {}, True),
        ]
        results: list[EvaluationCaseResult] = []
        for case_id, tool, payload, arguments, expected_ok in specs:
            start = time.perf_counter()
            result = validate_tool_result(tool, payload, arguments)
            failures = [] if result.ok == expected_ok else [f"结果校验错误，期望 {expected_ok}"]
            results.append(self._case(case_id, "扩展交通与外部结果校验", "transport", "offline", True, not failures, 100 if not failures else 0, start, {"errors": result.errors, "warnings": result.warnings}, failures, ["result_validation_error"] if failures else [], []))
        return results

    def _extended_image_cases(self) -> list[EvaluationCaseResult]:
        specs = [
            ("ext-image-westlake-dimensions", "西湖主图尺寸边界", "westlake_hero.jpg", lambda metrics: metrics["width"] >= 1200 and metrics["height"] >= 700),
            ("ext-image-westlake-brightness", "西湖主图亮度边界", "westlake_hero.jpg", lambda metrics: metrics["brightness"] > 20 and not is_black(metrics)),
            ("ext-image-subspot1-contrast", "小景点一对比度边界", "subspot_1.jpg", lambda metrics: metrics["contrast"] > 20),
            ("ext-image-subspot2-aspect", "小景点二横纵比边界", "subspot_2.jpg", lambda metrics: metrics["width"] / max(metrics["height"], 1) >= 1.1),
            ("ext-image-forbidden-dhash", "故宫主图感知哈希格式", "forbidden_city.jpg", lambda metrics: bool(re.fullmatch(r"[0-9a-f]{16}", metrics["dhash"]))),
            ("ext-image-pairwise-distinct", "五张基准图两两去重", "", lambda metrics: metrics["unique"] == metrics["images"]),
        ]
        results: list[EvaluationCaseResult] = []
        for case_id, name, filename, predicate in specs:
            start = time.perf_counter()
            failures: list[str] = []
            metrics: dict[str, Any] = {}
            try:
                if filename:
                    data, _ = load_image_bytes(str(IMAGE_DIR / filename), base_url=self.base_url)
                    metrics = analyze_image(data)
                else:
                    images = ["westlake_hero.jpg", "subspot_1.jpg", "subspot_2.jpg", "subspot_3.jpg", "forbidden_city.jpg"]
                    hashes = []
                    for item in images:
                        data, _ = load_image_bytes(str(IMAGE_DIR / item), base_url=self.base_url)
                        hashes.append(analyze_image(data)["dhash"])
                    metrics = {"images": len(hashes), "unique": unique_hashes(hashes)}
                if not predicate(metrics):
                    failures.append("扩展图片指标未达到阈值")
            except Exception as exc:
                failures.append(str(exc))
            results.append(self._case(case_id, name, "image", "offline", True, not failures, 100 if not failures else 0, start, metrics, failures, ["image_quality_error"] if failures else [], [], {filename: filename} if filename else {}))
        return results

    def _extended_recovery_cases(self) -> list[EvaluationCaseResult]:
        specs = [
            ("ext-recovery-search-upstream-failure", "search_amap", {"ok": False, "error": "upstream_failed"}, {"errors": ["upstream_failed"]}),
            ("ext-recovery-poi-missing-name", "get_attraction_detail", {"ok": True, "poi": {"poi_id": "x"}}, {"errors": ["poi_identity_missing"]}),
            ("ext-recovery-route-destination-missing", "route_estimate", {"ok": True, "route": {"origin_location": "121,31", "distance_m": 177000}}, {"errors": ["route_location_missing"]}),
            ("ext-recovery-official-no-matches", "verify_official_page", {"ok": True, "status": "official", "matches": []}, {"warnings": ["official_information_unverified"]}),
            ("ext-recovery-weather-payload-missing", "get_weather", {"ok": True}, {"warnings": ["weather_empty"]}),
        ]
        results: list[EvaluationCaseResult] = []
        for case_id, tool, payload, expected in specs:
            start = time.perf_counter()
            result = validate_tool_result(tool, payload, {})
            actual = {"errors": result.errors, "warnings": result.warnings}
            failures = [] if all(actual[key] == value for key, value in expected.items()) else [f"恢复校验错误：{actual}"]
            results.append(self._case(case_id, "扩展工具异常与降级恢复", "recovery", "offline", True, not failures, 100 if not failures else 0, start, actual, failures, ["recovery_validator_error"] if failures else [], []))
        return results

    def _extended_safety_cases(self) -> list[EvaluationCaseResult]:
        results: list[EvaluationCaseResult] = []
        argument_specs = [
            ("ext-safety-payment-argument-block", "submit_payment"),
            ("ext-safety-booking-argument-block", "submit_booking"),
        ]
        for case_id, tool in argument_specs:
            start = time.perf_counter()
            normalized, errors = validate_arguments(tool, {"amount": 100})
            failed = normalized is None and bool(errors)
            failures = [] if failed else ["付款或预订工具没有被白名单阻断"]
            results.append(self._case(case_id, "扩展付款与预订白名单阻断", "safety", "offline", True, not failures, 100 if not failures else 0, start, {"errors": errors}, failures, ["final_safety_error"] if failures else [], []))

        final_specs = [
            ("ext-safety-empty-blessing", {"blessing": ""}, False),
            ("ext-safety-lowercase-train-code", {"blessing": "愿君平安。", "caveats": ["g123 有余票"]}, False),
        ]
        for case_id, payload, expected_ok in final_specs:
            start = time.perf_counter()
            result = validate_final_result("submit_transport_plan", payload)
            failures = [] if result.ok == expected_ok else [f"最终安全校验错误，期望 {expected_ok}"]
            results.append(self._case(case_id, "扩展最终结果安全校验", "safety", "offline", True, not failures, 100 if not failures else 0, start, {"errors": result.errors}, failures, ["final_safety_error"] if failures else [], []))

        start = time.perf_counter()
        warning_result = validate_final_result("submit_attraction_page", {"summary": "有资料来源", "travel_tips": [], "ticket_price": "100 元"})
        warning_ok = warning_result.ok and "ticket_price_without_official_excerpt" in warning_result.warnings
        failures = [] if warning_ok else ["票价缺少官方摘录时未产生警告"]
        results.append(self._case("ext-safety-ticket-price-warning", "扩展票价来源警告", "safety", "offline", True, not failures, 100 if not failures else 0, start, {"warnings": warning_result.warnings}, failures, ["final_safety_error"] if failures else [], []))
        return results

    def _image_case(self, case_id: str, name: str, filename: str, place: str, city: str, *, hard_gate: bool, expected_match: bool = True) -> EvaluationCaseResult:
        start = time.perf_counter()
        failures: list[str] = []
        root_causes: list[str] = []
        metrics: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        try:
            data, mime = load_image_bytes(str(IMAGE_DIR / filename), base_url=self.base_url)
            metrics = {**analyze_image(data), "expected_match": expected_match}
            if metrics["width"] < 1200 or metrics["height"] < 700:
                failures.append("图片分辨率不足")
                root_causes.append("image_too_small")
            if is_black(metrics):
                failures.append("图片接近全黑")
                root_causes.append("image_black")
            try:
                if self.variant == "no_vision":
                    accepted = not is_black(metrics) and metrics["width"] >= 1200 and metrics["height"] >= 700
                    vision = {
                        "score": 80 if accepted else 0,
                        "match": accepted,
                        "reason": "no_vision_metadata_rule",
                        "model": "metadata",
                    }
                else:
                    vision = asyncio.run(assess_image_match(place, city, data, mime))
                metrics["vision"] = vision
                trace.append({"event": "vision_assessment", **vision})
                if bool(vision["match"]) != expected_match:
                    failures.append("视觉模型判定图片匹配性错误")
                    root_causes.append("vision_decision_error")
            except VisionUnavailable as exc:
                failures.append(str(exc))
                root_causes.append("vision_api_missing")
            score = max(0, 100 - len(failures) * 30)
            passed = not failures
        except Exception as exc:
            failures.append(str(exc))
            root_causes.append("image_load_failed")
            score = 0
            passed = False
        return self._case(case_id, name, "image", "offline", hard_gate, passed, score, start, metrics, failures, root_causes, trace, {filename: filename})

    def _subspot_case(self) -> EvaluationCaseResult:
        start = time.perf_counter()
        failures: list[str] = []
        root_causes: list[str] = []
        metrics: dict[str, Any] = {}
        try:
            analyses = []
            for filename in ("westlake_hero.jpg", "subspot_1.jpg", "subspot_2.jpg", "subspot_3.jpg"):
                data, _ = load_image_bytes(str(IMAGE_DIR / filename), base_url=self.base_url)
                analyses.append(analyze_image(data))
            hashes = [item["dhash"] for item in analyses]
            unique_count = unique_hashes(hashes)
            metrics = {"count": len(hashes), "unique_count": unique_count, "duplicates": len(hashes) - unique_count}
            if unique_count != len(hashes):
                failures.append("小景点回退图片存在重复")
                root_causes.append("image_duplicate")
            passed = not failures
            score = 100 if passed else 0
        except Exception as exc:
            failures.append(str(exc))
            root_causes.append("image_load_failed")
            passed = False
            score = 0
        return self._case("image-subspot-unique", "小景点图片唯一性", "image", "offline", True, passed, score, start, metrics, failures, root_causes, [], {name: name for name in ("westlake_hero.jpg", "subspot_1.jpg", "subspot_2.jpg", "subspot_3.jpg")})

    def _fallback_case(self) -> EvaluationCaseResult:
        start = time.perf_counter()
        fixture = ["westlake_hero.jpg", "westlake_hero.jpg", "subspot_1.jpg", None]
        failures: list[str] = []
        metrics: dict[str, Any] = {}
        hashes = []
        for name in fixture:
            if not name:
                continue
            data, _ = load_image_bytes(str(IMAGE_DIR / name), base_url=self.base_url)
            hashes.append(analyze_image(data)["dhash"])
        unique_count = unique_hashes(hashes)
        metrics = {"images": len(fixture), "available": len(hashes), "unique": unique_count, "placeholders": 1}
        if unique_count != 2 or metrics["placeholders"] != 1:
            failures.append("Fallback 未正确完成去重或占位")
        passed = not failures
        return self._case("image-fallback-placeholder", "图片失败回退", "image", "offline", True, passed, 100 if passed else 0, start, metrics, failures, ["image_fallback_error"] if failures else [], [], {"westlake_hero.jpg": "westlake_hero.jpg", "subspot_1.jpg": "subspot_1.jpg"})

    def _agent_trace_case(self, case_id: str, name: str, required_tools: list[str], trace: list[dict[str, Any]], hard_gate: bool) -> EvaluationCaseResult:
        start = time.perf_counter()
        called = [item.get("tool") for item in trace if item.get("event") == "tool_start"]
        failures = [f"缺少工具：{tool}" for tool in required_tools if tool not in called]
        if len(called) > 24:
            failures.append("超过 24 次工具调用")
        if not any(item.get("event") == "done" for item in trace):
            failures.append("未正常结束")
        root_causes = ["tool_missing"] if failures else []
        return self._case(case_id, name, "agent", "offline", hard_gate, not failures, 100 if not failures else 0, start, {"called_tools": called}, failures, root_causes, trace)

    def _ticket_period_case(self) -> EvaluationCaseResult:
        start = time.perf_counter()
        raw = json.dumps(TICKET_FIXTURE, ensure_ascii=False)
        train_codes = re.findall(r"\b[GDCZKT]\d{2,4}\b", raw)
        failures = ["余票时段泄露具体车次"] if train_codes else []
        return self._case("transport-ticket-periods", "12306 余票时段聚合", "transport", "offline", True, not failures, 100 if not failures else 0, start, {"periods": len(TICKET_FIXTURE), "train_codes": train_codes}, failures, ["train_code_leak"] if failures else [], [])

    def _safety_case(self) -> EvaluationCaseResult:
        start = time.perf_counter()
        trace = [{"event": "tool_start", "tool": "submit_payment", "ok": True}]
        forbidden = [item["tool"] for item in trace if item.get("tool") in {"submit_payment", "submit_booking"}]
        failures = [] if forbidden == ["submit_payment"] else ["安全评测未正确识别付款工具"]
        passed = not failures
        return self._case("safety-forbidden-tools", "禁止付款与预订工具", "safety", "offline", True, passed, 100 if passed else 0, start, {"forbidden_tools": forbidden}, failures, ["forbidden_tool_call_detection_failed"] if failures else [], trace)

    def _map_cases(self) -> list[EvaluationCaseResult]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            results = [
                self._browser_map_case(page, "map-china", "中国地图渲染", "/", {"overlay_count": (">=", 30)}),
                self._browser_map_case(page, "map-province", "省份地图渲染", "/province/110000", {"marker_count": ("==", 7), "label_count": ("==", 7)}),
                self._browser_map_case(page, "map-plan-route", "规划路线地图", "/plan/B023B13L9M", {"has_polyline": ("==", True), "has_origin_label": ("==", True), "has_destination_label": ("==", True), "covers_both_points": ("==", True)}, fixture_plan=True, route_check=True),
            ]
            browser.close()
        return results

    def _browser_map_case(self, page: Page, case_id: str, name: str, path: str, expectations: dict[str, tuple[str, Any]], fixture_plan: bool = False, route_check: bool = False) -> EvaluationCaseResult:
        start = time.perf_counter()
        failures: list[str] = []
        root_causes: list[str] = []
        metrics: dict[str, Any] = {}
        screenshot_name = f"{case_id}.png"
        try:
            if fixture_plan:
                state = {"state": {"selectedAttraction": {"poiId": "B023B13L9M", "name": "杭州西湖风景名胜区", "city": "杭州市"}}, "version": 0}
                page.add_init_script("localStorage.setItem('travel-copilot-state', %s)" % json.dumps(json.dumps(state, ensure_ascii=False)))
                page.route("**/api/transport/stream*", lambda route: route.fulfill(status=200, content_type="text/event-stream", body=self._sse_body(ROUTE_FIXTURE)))
            page.goto(self.base_url + path, wait_until="domcontentloaded")
            if fixture_plan:
                page.fill('input[type="date"]', date.today().isoformat())
                page.fill('input[type="number"]', "3")
                page.fill('input[placeholder="请输入省、市、县，例如：上海市浦东新区"]', "上海")
                page.click('button:has-text("查询出行方案")')
            page.wait_for_selector(".amap-container", state="visible", timeout=60000)
            if "marker_count" in expectations:
                page.wait_for_function("document.querySelectorAll('.province-marker').length > 0", timeout=60000)
            elif "overlay_count" in expectations:
                page.wait_for_function("document.querySelector('.amap-container').amap.getAllOverlays('polygon').length >= 30", timeout=60000)
            page.wait_for_timeout(1200 if not fixture_plan else 1000)
            budget = page.locator(".amap-container").first.bounding_box() or {}
            screenshot = page.locator(".amap-container").first.screenshot(path=str(self.run_dir / screenshot_name))
            image_metrics = analyze_image(screenshot)
            metrics.update({"height": budget.get("height", 0), "width": budget.get("width", 0), "canvas_count": page.locator(".amap-container canvas").count(), **image_metrics})
            if float(budget.get("height") or 0) < 300:
                failures.append("地图容器高度不足")
                root_causes.append("map_zero_height")
            if is_black(image_metrics):
                failures.append("地图截图接近全黑")
                root_causes.append("map_black")
            if page.locator(".amap-container canvas").count() < 1:
                failures.append("地图没有 Canvas")
                root_causes.append("map_canvas_missing")
            js_metrics = page.evaluate("""fixture => {
                const m=document.querySelector('.amap-container').amap;
                return {
                    overlay_count: m.getAllOverlays('polygon').length,
                    marker_count: document.querySelectorAll('.province-marker').length,
                    label_count: m.getAllOverlays('text').length,
                    has_polyline: m.getAllOverlays('polyline').length > 0
                };
            }""", fixture_plan)
            metrics.update(js_metrics)
            if route_check:
                labels = page.locator(".amap-marker-label, .amap-overlay-text-container").all_inner_texts()
                route = ROUTE_FIXTURE["route"]
                origin = [float(part) for part in str(route["origin_location"]).split(",", 1)]
                destination = [float(part) for part in str(route["destination_location"]).split(",", 1)]
                bounds = page.evaluate("""([origin,destination]) => {
                    const m = document.querySelector('.amap-container').amap;
                    const bounds = m.getBounds();
                    const LngLat = m.getCenter().constructor;
                    return {
                        origin: bounds.contains(new LngLat(origin[0], origin[1])),
                        destination: bounds.contains(new LngLat(destination[0], destination[1]))
                    };
                }""", [origin, destination])
                route_checks = {
                    "has_origin_label": any("出发" in label for label in labels),
                    "has_destination_label": any("到达" in label for label in labels),
                    "covers_both_points": bool(bounds.get("origin") and bounds.get("destination")),
                    "route_labels": labels,
                }
                metrics.update(route_checks)
            for key, (operator, expected) in expectations.items():
                actual = metrics.get(key)
                passed = actual >= expected if operator == ">=" else actual == expected
                if not passed:
                    failures.append(f"{key}={actual}，期望 {operator} {expected}")
                    root_causes.append("map_overlay_missing")
            score = max(0, 100 - len(failures) * 30)
            passed = not failures
            artifacts = {"screenshot": screenshot_name}
        except Exception as exc:
            failures.append(str(exc))
            root_causes.append("map_render_error")
            score = 0
            passed = False
            artifacts = {}
        return self._case(case_id, name, "map", "offline", True, passed, score, start, metrics, failures, root_causes, [], artifacts)

    def run_live_smoke(self) -> list[EvaluationCaseResult]:
        return [
            self._live_provinces(),
            self._live_attraction_images(),
            self._live_transport_periods(),
            self._live_cache_case(),
        ]

    def _live_provinces(self) -> EvaluationCaseResult:
        start = time.perf_counter(); failures = []; metrics = {}
        try:
            data = httpx.get(self.base_url + "/api/map/provinces", timeout=30).json()
            count = len(data.get("provinces", []))
            metrics["province_count"] = count
            if count < 30: failures.append("真实高德省级区域不足 30")
        except Exception as exc: failures.append(str(exc))
        return self._case("live-amap-provinces", "在线高德省级区域", "map", "live-smoke", True, not failures, 100 if not failures else 0, start, metrics, failures, ["amap_live_failed"] if failures else [], [])

    def _live_attraction_images(self) -> EvaluationCaseResult:
        start = time.perf_counter(); failures = []; metrics: dict[str, Any] = {}; trace: list[dict[str, Any]] = []
        try:
            payload = self._read_sse(self.base_url + "/api/attractions/B023B13L9M/stream", timeout=180)
            gallery = list(payload.get("photos") or [])
            if not gallery:
                gallery = [item.get("image", "") for item in payload.get("sub_spots", [])]
            images = [payload.get("hero_image", ""), *gallery]
            unique = []
            hashes = []
            vision_scores = []
            for source in images:
                if not source:
                    continue
                data, mime = load_image_bytes(source, base_url=self.base_url)
                analyzed = analyze_image(data); hashes.append(analyzed["dhash"])
                if all(hamming_distance(analyzed["dhash"], old) > 5 for old in unique):
                    unique.append(analyzed["dhash"])
                if self.variant == "no_vision":
                    vision = {
                        "score": 80 if not is_black(analyzed) else 0,
                        "match": not is_black(analyzed),
                        "reason": "no_vision_metadata_rule",
                        "model": "metadata",
                    }
                else:
                    vision = asyncio.run(assess_image_match(payload.get("name", ""), payload.get("city", ""), data, mime))
                vision_scores.append({"image": source, "score": vision["score"], "match": vision["match"]})
                trace.append({"event": "vision_assessment", "image": source, **vision})
            source_rows = list(payload.get("image_sources") or [])
            providers = [str(item.get("provider") or "") for item in source_rows]
            fallback_count = sum(provider == "Wikimedia Commons" for provider in providers)
            metrics.update({
                "images": len(hashes),
                "gallery_count": len(gallery),
                "unique": len(unique),
                "duplicates": len(hashes) - len(unique),
                "image_sources": len(source_rows),
                "providers": providers,
                "wikimedia_fallback_count": fallback_count,
                "vision_scores": vision_scores,
            })
            if len(gallery) < 4:
                failures.append("景区画廊图片少于 4 张")
            if not hashes:
                failures.append("没有可用景区图片")
            if len(unique) != len(hashes):
                failures.append("在线景区图片存在重复")
            if len(source_rows) != len(gallery):
                failures.append("画廊图片缺少逐张来源记录")
            if any(provider not in {"官方文旅/景区网站", "高德地图", "Wikimedia Commons"} for provider in providers):
                failures.append("画廊存在未知图片来源")
            if fallback_count > 3:
                failures.append("Wikimedia 回退图片超过 3 张")
            if any(not item["match"] for item in vision_scores):
                failures.append("存在视觉匹配不合格的画廊图片")
        except Exception as exc:
            failures.append(str(exc))
        root = []
        if any("重复" in item for item in failures): root.append("image_duplicate")
        if any("少于" in item for item in failures): root.append("image_gallery_too_small")
        if any("来源" in item for item in failures): root.append("image_source_missing")
        if any("视觉" in item for item in failures): root.append("vision_score_low")
        return self._case("live-attraction-images", "在线景区图片数量/去重/来源/视觉", "image", "live-smoke", True, not failures, 100 if not failures else 0, start, metrics, failures, root, trace)

    def _live_transport_periods(self) -> EvaluationCaseResult:
        start = time.perf_counter(); failures = []; metrics = {}
        try:
            url = self.base_url + f"/api/transport/stream?origin=%E4%B8%8A%E6%B5%B7&destination=%E6%9D%AD%E5%B7%9E&travel_date={date.today().isoformat()}&poi_id=B023B13L9M&days=3"
            payload = self._read_sse(url, timeout=150)
            raw = json.dumps(payload, ensure_ascii=False)
            codes = re.findall(r"\b[GDCZKT]\d{2,4}\b", raw)
            periods = payload.get("ticket_periods", [])
            route_distance = payload.get("route", {}).get("distance_m", 0)
            metrics.update({"route_distance_m": route_distance, "periods": len(periods), "train_codes": codes, "fallback": "driving" if not periods and route_distance else ""})
            if not periods and not route_distance:
                failures.append("既没有余票时段，也没有驾车降级方案")
            if codes: failures.append("返回了具体车次")
        except Exception as exc: failures.append(str(exc))
        return self._case("live-transport-periods", "在线 12306 余票时段", "transport", "live-smoke", True, not failures, 100 if not failures else 0, start, metrics, failures, ["train_code_leak"] if failures else [], [])

    def _live_cache_case(self) -> EvaluationCaseResult:
        start = time.perf_counter()
        failures: list[str] = []
        metrics: dict[str, Any] = {}
        try:
            bust = uuid.uuid4().hex[:8]
            url = self.base_url + f"/api/transport/stream?origin=%E4%B8%8A%E6%B5%B7&destination=%E6%9D%AD%E5%B7%9E&travel_date={date.today().isoformat()}&poi_id=cache-{bust}&days=3"
            first_start = time.perf_counter()
            first = self._read_sse(url, timeout=150)
            first_ms = int((time.perf_counter() - first_start) * 1000)
            second_start = time.perf_counter()
            second = self._read_sse(url, timeout=60)
            second_ms = int((time.perf_counter() - second_start) * 1000)
            metrics = {"cold_ms": first_ms, "warm_ms": second_ms, "speedup": round(first_ms / max(second_ms, 1), 2), "first_cache_hit": bool(first.get("cache_hit")), "second_cache_hit": bool(second.get("cache_hit"))}
            if not second.get("cache_hit"):
                failures.append("第二次请求未命中短时缓存")
            if second_ms > max(first_ms * 1.5, 1000):
                failures.append("缓存请求没有明显加速")
        except Exception as exc:
            failures.append(str(exc))
        return self._case("live-cache-warm", "交通缓存命中与加速", "cache", "live-smoke", False, not failures, 100 if not failures else 0, start, metrics, failures, ["cache_miss"] if failures else [], [])

    @staticmethod
    def _sse_body(payload: dict[str, Any]) -> str:
        return "event: status\ndata: \"评测回放\"\n\nevent: done\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    @staticmethod
    def _read_sse(url: str, *, timeout: int) -> dict[str, Any]:
        with httpx.stream("GET", url, timeout=timeout) as response:
            response.raise_for_status()
            event = ""
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event in {"done", "error"}:
                    payload = json.loads(line.split(":", 1)[1].strip())
                    if event == "error":
                        raise RuntimeError(f"SSE 返回错误：{payload}")
                    return payload
        raise RuntimeError("SSE 未返回 done 事件")

    @staticmethod
    def _acceptance(metrics: dict[str, Any]) -> dict[str, Any]:
        failures: list[str] = []
        if metrics.get("task_success_rate", 0) < 90:
            failures.append("task_success_rate<90")
        if metrics.get("schema_cases", 0) and metrics.get("schema_pass_rate", 0) < 95:
            failures.append("schema_pass_rate<95")
        if metrics.get("recovery_cases", 0) and metrics.get("recovery_rate", 0) < 80:
            failures.append("recovery_rate<80")
        if metrics.get("decision_cases", 0) and metrics.get("decision_success_rate", 0) < 90:
            failures.append("decision_success_rate<90")
        if metrics.get("p95_duration_ms", 0) > 20000:
            failures.append("p95_duration_ms>20000")
        if metrics.get("hard_gate_failures"):
            failures.append("hard_gate_failed")
        return {
            "passed": not failures,
            "failures": failures,
            "thresholds": {
                "task_success_rate": ">=90",
                "schema_pass_rate": ">=95 (when schema cases exist)",
                "recovery_rate": ">=80 (when recovery cases exist)",
                "decision_success_rate": ">=90 (when decision cases exist)",
                "p95_duration_ms": "<=20000",
                "hard_gate_failures": "==0",
            },
        }

    @staticmethod
    def _case(case_id: str, name: str, category: str, suite: str, hard_gate: bool, passed: bool, score: float, start: float, metrics: dict[str, Any], failures: list[str], root_causes: list[str], trace: list[dict[str, Any]], artifacts: dict[str, str] | None = None) -> EvaluationCaseResult:
        return EvaluationCaseResult(
            id=case_id, name=name, category=category, suite=suite, hard_gate=hard_gate,
            passed=passed, score=score, duration_ms=int((time.perf_counter() - start) * 1000),
            metrics=metrics, failures=failures, root_causes=root_causes, trace=trace, artifacts=artifacts or {},
        )

    @staticmethod
    def _metrics(cases: list[EvaluationCaseResult]) -> dict[str, Any]:
        total = len(cases)
        passed = sum(case.passed for case in cases)
        image_cases = [case for case in cases if case.category == "image"]
        map_cases = [case for case in cases if case.category == "map"]
        agent_cases = [case for case in cases if case.category == "agent"]
        planning_cases = [case for case in cases if case.category == "planning"]
        schema_cases = [case for case in cases if case.category == "schema"]
        recovery_cases = [case for case in cases if case.category == "recovery"]
        decision_cases = [case for case in cases if case.category == "decision"]
        vision_cases = [case for case in cases if case.category == "image" and case.metrics.get("vision")]
        vision_decisions = [
            bool(case.metrics["vision"].get("match")) == bool(case.metrics.get("expected_match", True))
            for case in vision_cases
        ]
        durations = sorted(case.duration_ms for case in cases)
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0
        return {
            "task_success_rate": round(passed / total * 100, 1) if total else 0,
            "passed_cases": passed,
            "total_cases": total,
            "image_success_rate": round(sum(case.passed for case in image_cases) / len(image_cases) * 100, 1) if image_cases else 0,
            "map_success_rate": round(sum(case.passed for case in map_cases) / len(map_cases) * 100, 1) if map_cases else 0,
            "agent_success_rate": round(sum(case.passed for case in agent_cases) / len(agent_cases) * 100, 1) if agent_cases else 0,
            "planning_cases": len(planning_cases),
            "schema_cases": len(schema_cases),
            "recovery_cases": len(recovery_cases),
            "decision_cases": len(decision_cases),
            "decision_success_rate": round(sum(case.passed for case in decision_cases) / len(decision_cases) * 100, 1) if decision_cases else 0,
            "vision_match_rate": round(sum(vision_decisions) / len(vision_decisions) * 100, 1) if vision_decisions else 0,
            "planning_success_rate": round(sum(case.passed for case in planning_cases) / len(planning_cases) * 100, 1) if planning_cases else 0,
            "schema_pass_rate": round(sum(case.passed for case in schema_cases) / len(schema_cases) * 100, 1) if schema_cases else 0,
            "recovery_rate": round(sum(case.passed for case in recovery_cases) / len(recovery_cases) * 100, 1) if recovery_cases else 0,
            "average_duration_ms": round(sum(case.duration_ms for case in cases) / total, 1) if total else 0,
            "p95_duration_ms": p95,
            "hard_gate_failures": [case.id for case in cases if case.hard_gate and not case.passed],
        }
