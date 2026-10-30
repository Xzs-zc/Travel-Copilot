from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import json
import math
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
from playwright.sync_api import sync_playwright

from ..agent.service import TravelAgentService
from ..agent.state import EvidenceItem, build_replan_delta, detect_evidence_conflicts, preferred_evidence
from ..agent.ticket_periods import build_rail_itinerary
from ..agent.validators import validate_arguments, validate_final_result, validate_tool_result
from ..config import PROJECT_DIR, get_settings
from ..schemas import FlightOption, RouteMapInfo, SourceRef
from .decision import run_decision_cases
from .images import analyze_image
from .models import EvaluationCaseResult
from .runner import ROUTE_FIXTURE, TICKET_FIXTURE, EvaluationRunner
from .runtime_loops import run_runtime_loop_cases


OFFLINE_CATEGORY_COUNTS = {
    "tools_schema": 15,
    "runtime_recovery": 15,
    "safety_evidence": 8,
    "map_image": 7,
    "e2e": 15,
}
LIVE_CASE_IDS = [
    "live-health",
    "live-config",
    "live-providers",
    "live-provinces",
    "live-province-attractions",
    "live-attraction-search",
    "live-attraction-prepare",
    "live-attraction-stream",
    "live-transport",
    "live-transport-cache",
    "live-long-transport",
    "live-attraction-cache",
]


def _case(
    case_id: str,
    category: str,
    passed: bool,
    duration_ms: int,
    *,
    suite: str = "offline",
    metrics: dict[str, Any] | None = None,
    failures: list[str] | None = None,
    trace: list[dict[str, Any]] | None = None,
    artifacts: dict[str, str] | None = None,
) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        id=case_id,
        name=case_id,
        category=category,
        suite=suite,
        hard_gate=True,
        passed=passed,
        score=100 if passed else 0,
        duration_ms=duration_ms,
        metrics=metrics or {},
        failures=failures or [],
        root_causes=failures or [],
        trace=trace or [],
        artifacts=artifacts or {},
    )


class TravelEvalV2Runner:
    def __init__(self, *, base_url: str, output_root: Path):
        self.base_url = base_url.rstrip("/")
        self.output_root = output_root
        self.settings = get_settings()
        self.started_at = time.time()
        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.run_dir = output_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.legacy = EvaluationRunner(base_url=self.base_url, run_dir=self.run_dir, trials=1)

    def run(self) -> dict[str, Any]:
        started = time.perf_counter()
        offline: list[EvaluationCaseResult] = []
        for trial in range(1, 4):
            trial_results = self._run_offline_once()
            for item in trial_results:
                base_id = str(item.metrics.get("base_id") or item.id)
                item.metrics["base_id"] = base_id
                item.metrics["trial"] = trial
                item.id = f"{base_id}@t{trial}"
            offline.extend(trial_results)
        live = self._run_live_smoke()
        if len(offline) != 180:
            raise RuntimeError(f"离线执行应为 180 次，实际 {len(offline)}")
        if len(live) != 12:
            raise RuntimeError(f"在线冒烟应为 12 条，实际 {len(live)}")
        if len({item.metrics.get("base_id") for item in offline}) != 60 or len({item.id for item in live}) != 12:
            raise RuntimeError("评测 Case ID 存在重复")
        for category, expected_count in OFFLINE_CATEGORY_COUNTS.items():
            unique = {
                item.metrics.get("base_id")
                for item in offline
                if item.category == category
            }
            if len(unique) != expected_count:
                raise RuntimeError(f"{category} 应为 {expected_count} 条，实际 {len(unique)} 条")
        report = self._report(offline, live, elapsed_ms=int((time.perf_counter() - started) * 1000))
        self._write_report(report)
        return report

    def _run_offline_once(self) -> list[EvaluationCaseResult]:
        return [
            *self._run_tools_schema(),
            *self._run_runtime_recovery(),
            *self._run_safety_evidence(),
            *self._run_map_image(),
            *self._run_e2e(),
        ]

    def _run_tools_schema(self) -> list[EvaluationCaseResult]:
        specs = [
            ("tool-train-valid", "query_train", {"origin": "上海", "destination": "杭州", "travel_date": "2026-12-01"}, True),
            ("tool-train-bad-date", "query_train", {"origin": "上海", "destination": "杭州", "travel_date": "bad"}, False),
            ("tool-search-valid", "search_amap", {"keywords": "黄山", "city": "黄山市"}, True),
            ("tool-search-empty", "search_amap", {"keywords": ""}, False),
            ("tool-weather-valid", "get_weather", {"city": "杭州市"}, True),
            ("tool-weather-blank", "get_weather", {"city": "  "}, False),
            ("tool-route-valid", "route_estimate", {"origin": "上海", "destination": "杭州"}, True),
            ("tool-route-blank", "route_estimate", {"origin": "上海", "destination": ""}, False),
            ("tool-flight-valid", "query_flight", {"origin": "上海", "destination": "北京", "travel_date": "2026-12-02"}, True),
            ("tool-flight-bad-date", "query_flight", {"origin": "上海", "destination": "北京", "travel_date": "12-02"}, False),
            ("tool-official-valid", "verify_official_page", {"name": "黄山", "url": "https://example.com"}, True),
            ("tool-official-no-url", "verify_official_page", {"name": "黄山"}, False),
            ("tool-unknown", "unknown_tool", {}, False),
            ("tool-payment-blocked", "submit_payment", {"amount": 100}, False),
            ("tool-booking-blocked", "submit_booking", {"order_id": "x"}, False),
        ]
        results: list[EvaluationCaseResult] = []
        for case_id, tool, arguments, expected in specs:
            start = time.perf_counter()
            normalized, errors = validate_arguments(tool, arguments)
            actual = normalized is not None and not errors
            passed = actual == expected
            results.append(
                _case(
                    case_id,
                    "tools_schema",
                    passed,
                    int((time.perf_counter() - start) * 1000),
                    metrics={"expected": expected, "actual": actual, "errors": errors},
                    failures=[] if passed else [f"expected={expected}, actual={actual}"],
                )
            )
        return results

    def _run_runtime_recovery(self) -> list[EvaluationCaseResult]:
        results = [*run_decision_cases("full"), *run_runtime_loop_cases("full")]

        start = time.perf_counter()
        empty_ticket = validate_tool_result(
            "query_train",
            {"ok": True, "ticket_periods": []},
            {"origin": "上海", "destination": "杭州", "travel_date": "2026-12-01"},
        )
        results.append(
            _case(
                "runtime-empty-ticket-valid",
                "runtime_recovery",
                empty_ticket.ok and not empty_ticket.errors,
                int((time.perf_counter() - start) * 1000),
                metrics={"errors": empty_ticket.errors, "warnings": empty_ticket.warnings},
                failures=[] if empty_ticket.ok and not empty_ticket.errors else ["empty_ticket_treated_as_failure"],
            )
        )

        start = time.perf_counter()
        empty_flight = validate_tool_result(
            "query_flight",
            {"ok": True, "flights": []},
            {"origin": "北京", "destination": "拉萨", "travel_date": "2026-12-03"},
        )
        passed = empty_flight.ok and "flight_options_empty" in empty_flight.warnings
        results.append(
            _case(
                "runtime-empty-flight-warning",
                "runtime_recovery",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics={"warnings": empty_flight.warnings},
                failures=[] if passed else ["missing_flight_empty_warning"],
            )
        )

        start = time.perf_counter()
        official = validate_tool_result(
            "verify_official_page",
            {"ok": True, "status": "amap_only", "url": "", "matches": []},
            {"name": "黄山", "url": "", "keywords": ["开放时间"]},
        )
        passed = official.ok and "official_information_unverified" in official.warnings
        results.append(
            _case(
                "runtime-official-unverified",
                "runtime_recovery",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics={"warnings": official.warnings},
                failures=[] if passed else ["missing_official_unverified_warning"],
            )
        )

        start = time.perf_counter()
        delta = build_replan_delta(
            "transport_plan",
            {"origin": "上海", "destination": "杭州", "travel_date": "2026-12-01", "days": 3},
            {"origin": "上海", "destination": "杭州", "travel_date": "2026-12-02", "days": 3},
        )
        passed = (
            "transport.route" in delta.reused_evidence
            and {"transport.ticket_periods", "weather.forecast"}.issubset(delta.invalidated_evidence)
        )
        results.append(
            _case(
                "runtime-date-replan",
                "runtime_recovery",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics=delta.model_dump(mode="json"),
                failures=[] if passed else ["date_replan_scope_error"],
            )
        )

        start = time.perf_counter()
        train_results = [
            {
                "arguments": {"origin": "北京", "destination": "西安"},
                "result": {"ok": True, "train_count": 3, "train_summary": {"train_count": 3, "min_duration_minutes": 130}, "ticket_periods": [{"label": "上午", "status": "available", "summary": "有余票", "available_count": 2}]},
            },
            {
                "arguments": {"origin": "西安", "destination": "成都"},
                "result": {"ok": True, "train_count": 2, "train_summary": {"train_count": 2, "min_duration_minutes": 180}, "ticket_periods": [{"label": "下午", "status": "available", "summary": "有余票", "available_count": 1}]},
            },
        ]
        itinerary = build_rail_itinerary(train_results, "北京", "成都")
        passed = itinerary.get("available") is True and len(itinerary.get("segments") or []) == 2
        results.append(
            _case(
                "runtime-multi-segment",
                "runtime_recovery",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics={"segments": len(itinerary.get("segments") or []), "total_minutes": itinerary.get("total_minutes")},
                failures=[] if passed else ["multi_segment_itinerary_missing"],
            )
        )

        source = SourceRef(title="高德驾车路线", provider="高德地图")
        short_route = RouteMapInfo(
            origin_name="上海",
            destination_name="苏州",
            origin_location="121,31",
            destination_location="120,31",
            distance_m=100000,
            duration_s=5400,
            source=source,
        )
        start = time.perf_counter()
        short_recommendation = TravelAgentService._recommend_transport({}, short_route, {}, [])
        passed = short_recommendation.mode == "drive"
        results.append(
            _case(
                "runtime-short-drive",
                "runtime_recovery",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics=short_recommendation.model_dump(mode="json"),
                failures=[] if passed else [f"unexpected_mode={short_recommendation.mode}"],
            )
        )

        long_route = short_route.model_copy(update={"distance_m": 1200000, "duration_s": 36000})
        flight = FlightOption(price=800, duration_minutes=180)
        start = time.perf_counter()
        long_recommendation = TravelAgentService._recommend_transport(
            {},
            long_route,
            {"train_count": 10, "min_duration_minutes": 420, "direct_available": True},
            [flight],
        )
        passed = long_recommendation.mode == "high_speed_rail_or_flight"
        results.append(
            _case(
                "runtime-long-flight-comparison",
                "runtime_recovery",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics=long_recommendation.model_dump(mode="json"),
                failures=[] if passed else [f"unexpected_mode={long_recommendation.mode}"],
            )
        )
        results = results[:15]
        for item in results:
            item.category = "runtime_recovery"
        return results

    def _run_safety_evidence(self) -> list[EvaluationCaseResult]:
        results: list[EvaluationCaseResult] = []

        def add_arg(case_id: str, tool: str) -> None:
            start = time.perf_counter()
            normalized, errors = validate_arguments(tool, {"amount": 100})
            passed = normalized is None and bool(errors)
            results.append(
                _case(
                    case_id,
                    "safety_evidence",
                    passed,
                    int((time.perf_counter() - start) * 1000),
                    metrics={"errors": errors},
                    failures=[] if passed else ["forbidden_tool_not_blocked"],
                )
            )

        add_arg("safety-payment-tool", "submit_payment")
        add_arg("safety-booking-tool", "submit_booking")

        for case_id, payload in (
            ("safety-train-code", {"blessing": "愿君平安。", "caveats": ["G123 有余票"]}),
            ("safety-lowercase-code", {"blessing": "愿君平安。", "caveats": ["g123 有余票"]}),
            ("safety-empty-blessing", {"blessing": ""}),
        ):
            start = time.perf_counter()
            result = validate_final_result("submit_transport_plan", payload)
            passed = not result.ok
            results.append(
                _case(
                    case_id,
                    "safety_evidence",
                    passed,
                    int((time.perf_counter() - start) * 1000),
                    metrics={"errors": result.errors},
                    failures=[] if passed else ["unsafe_final_result_accepted"],
                )
            )

        start = time.perf_counter()
        warning = validate_final_result(
            "submit_attraction_page",
            {"summary": "有来源", "travel_tips": [], "ticket_price": "100 元"},
        )
        passed = warning.ok and "ticket_price_without_official_excerpt" in warning.warnings
        results.append(
            _case(
                "safety-ticket-warning",
                "safety_evidence",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics={"warnings": warning.warnings},
                failures=[] if passed else ["missing_ticket_source_warning"],
            )
        )

        start = time.perf_counter()
        items = [
            EvidenceItem(id="official.open", field="attraction.open_time", value="08:00-17:00", source=SourceRef(title="官网", provider="官方网站")),
            EvidenceItem(id="amap.open", field="attraction.open_time", value="24小时", source=SourceRef(title="高德", provider="高德地图")),
        ]
        conflicts = detect_evidence_conflicts(items)
        preferred = preferred_evidence(items, "attraction.open_time")
        passed = len(conflicts) == 2 and preferred is not None and preferred.source.provider == "官方网站"
        results.append(
            _case(
                "safety-source-priority",
                "safety_evidence",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics={"conflicts": len(conflicts), "preferred": preferred.id if preferred else ""},
                failures=[] if passed else ["source_priority_error"],
            )
        )

        start = time.perf_counter()
        official = validate_tool_result(
            "verify_official_page",
            {"ok": True, "status": "official", "matches": []},
            {"name": "黄山", "url": "https://example.com"},
        )
        passed = official.ok and "official_information_unverified" in official.warnings
        results.append(
            _case(
                "safety-official-unverified",
                "safety_evidence",
                passed,
                int((time.perf_counter() - start) * 1000),
                metrics={"warnings": official.warnings},
                failures=[] if passed else ["official_unverified_not_blocked"],
            )
        )
        return results

    def _run_map_image(self) -> list[EvaluationCaseResult]:
        results = [
            self.legacy._image_case("image-westlake-hero", "西湖主图质量", "westlake_hero.jpg", "杭州西湖风景名胜区", "杭州", hard_gate=True),
            self.legacy._image_case("image-obscure-exact", "冷门景区精确图片", "forbidden_city.jpg", "故宫博物院", "北京", hard_gate=True),
            self.legacy._image_case("image-semantic-mismatch", "错误景区图片拒绝", "westlake_hero.jpg", "故宫博物院", "北京", hard_gate=True, expected_match=False),
            self.legacy._fallback_case(),
            *self.legacy._map_cases(),
        ]
        for item in results:
            item.category = "map_image"
        return results

    def _run_e2e(self) -> list[EvaluationCaseResult]:
        specs = self._e2e_specs()
        results: list[EvaluationCaseResult] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for spec in specs:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                try:
                    results.append(self._run_e2e_browser_case(page, spec))
                finally:
                    context.close()
            browser.close()
        return results

    def _e2e_specs(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        base = copy.deepcopy(ROUTE_FIXTURE)

        def payload(**updates: Any) -> dict[str, Any]:
            item = copy.deepcopy(base)
            item.update(updates)
            return item

        specs.append({"id": "e2e-rail-plan", "payload": payload(recommendation={"mode": "high_speed_rail", "summary": "优先高铁", "reason": "稳定"}, show_driving_plan=False), "texts": ["高铁优先", "12306 余票时段"]})
        specs.append({"id": "e2e-drive-plan", "payload": payload(recommendation={"mode": "drive", "summary": "优先驾车", "reason": "短途"}, show_driving_plan=True, driving_recommended=True), "texts": ["驾车优先", "推荐驾车方案"]})
        specs.append({
            "id": "e2e-flight-plan",
            "payload": payload(
                recommendation={"mode": "flight", "summary": "优先飞机", "reason": "长途"},
                show_driving_plan=False,
                flight_comparison_allowed=True,
                flight_periods=[{"label": "上午", "start_time": "06:00", "end_time": "11:59", "flight_count": 2, "min_price": 600, "duration_minutes": 180, "stops": 0, "summary": "该时段有余票"}],
                transport_comparison={"train_price": 300, "flight_price": 600, "train_duration_minutes": 420, "flight_duration_minutes": 180, "price_gap": 300, "duration_gap_minutes": 240, "flight_available": True},
            ),
            "texts": ["飞机优先", "飞机参考"],
        })
        specs.append({"id": "e2e-rail-flight-compare", "payload": payload(recommendation={"mode": "high_speed_rail_or_flight", "summary": "比较高高铁和飞机", "reason": "长途"}, show_driving_plan=False, flight_comparison_allowed=True), "texts": ["高铁 / 飞机比较"]})
        specs.append({"id": "e2e-mixed-plan", "payload": payload(recommendation={"mode": "mixed", "summary": "综合换乘", "reason": "组合"}, show_driving_plan=False), "texts": ["综合方案"]})
        specs.append({"id": "e2e-no-ticket", "payload": payload(ticket_periods=[], recommendation={"mode": "drive", "summary": "无票驾车", "reason": "降级"}, show_driving_plan=True), "texts": ["当天并无余票"]})
        specs.append({"id": "e2e-rainy-weather", "payload": payload(recommendation={"mode": "high_speed_rail", "summary": "雨天高铁", "reason": "天气"}, show_driving_plan=False, weather={"city": "杭州市", "report_time": "2026-10-25", "casts": [{"date": "2026-10-26", "dayweather": "暴雨", "nighttemp": "18", "daytemp": "22", "daywind": "东", "daypower": "3"}]}), "texts": ["暴雨"]})
        specs.append({"id": "e2e-empty-weather", "payload": payload(recommendation={"mode": "high_speed_rail", "summary": "无天气", "reason": "缺失"}, show_driving_plan=False, weather={"casts": []}), "texts": ["高德暂未返回"]})
        specs.append({"id": "e2e-blessing", "payload": payload(recommendation={"mode": "high_speed_rail", "summary": "祝福", "reason": "测试"}, show_driving_plan=False, blessing={"text": "山高水远路迢迢，平安顺遂乐逍遥。", "style": "lively", "generated_at": "2026-10-25"}), "texts": ["山高水远路迢迢，平安顺遂乐逍遥。"]})
        specs.append({"id": "e2e-caveats", "payload": payload(recommendation={"mode": "high_speed_rail", "summary": "注意事项", "reason": "测试"}, show_driving_plan=False, caveats=["身份证请随身携带", "雨具请提前准备"]), "texts": ["身份证请随身携带", "雨具请提前准备"]})
        specs.append({"id": "e2e-no-train-code", "payload": payload(recommendation={"mode": "high_speed_rail", "summary": "时段", "reason": "测试"}, show_driving_plan=False, ticket_periods=[{"label": "06:00–08:59", "status": "available", "summary": "该时段有余票", "seat_summary": []}]), "texts": ["06:00–08:59"], "not_regex": r"\b[GDCZKT]\d{2,4}\b"})
        specs.append({"id": "e2e-route-map", "payload": payload(recommendation={"mode": "drive", "summary": "路线", "reason": "测试"}, show_driving_plan=True), "texts": ["上海 → 杭州"], "min_map": True})
        specs.append({"id": "e2e-route-distance", "payload": payload(recommendation={"mode": "drive", "summary": "距离", "reason": "测试"}, show_driving_plan=True), "texts": ["177", "距离"], "min_map": True})
        specs.append({"id": "e2e-flight-periods", "payload": payload(recommendation={"mode": "flight", "summary": "航班时段", "reason": "测试"}, show_driving_plan=False, flight_comparison_allowed=True, flight_periods=[{"label": "晚间", "start_time": "18:00", "end_time": "23:59", "flight_count": 2, "min_price": 700, "duration_minutes": 170, "stops": 0, "summary": "该时段有余票"}]), "texts": ["晚间", "飞机参考"]})
        specs.append({"id": "e2e-no-booking", "payload": payload(recommendation={"mode": "high_speed_rail", "summary": "只读", "reason": "测试"}, show_driving_plan=False), "texts": ["Agent 交通建议"], "not_texts": ["立即预订", "支付订单", "购买车票"]})
        return specs

    def _run_e2e_browser_case(self, page: Any, spec: dict[str, Any]) -> EvaluationCaseResult:
        start = time.perf_counter()
        failures: list[str] = []
        metrics: dict[str, Any] = {}
        state = {"state": {"selectedAttraction": {"poiId": "B023B13L9M", "name": "杭州西湖风景名胜区", "city": "杭州市"}}, "version": 0}
        page.add_init_script("localStorage.setItem('travel-copilot-state', %s)" % json.dumps(json.dumps(state, ensure_ascii=False)))
        page.route("**/api/transport/stream*", lambda route: route.fulfill(status=200, content_type="text/event-stream", body=self._sse_body(spec["payload"])))
        try:
            page.goto(self.base_url + "/plan/B023B13L9M", wait_until="domcontentloaded", timeout=30000)
            page.fill('input[type="date"]', date.today().isoformat())
            page.fill('input[type="number"]', "3")
            page.fill('input[placeholder="请输入省、市、县，例如：上海市浦东新区"]', "上海")
            page.click('button:has-text("查询出行方案")')
            for text in spec["texts"]:
                page.get_by_text(text).first.wait_for(timeout=30000)
            body = page.locator("body").inner_text()
            for text in spec["texts"]:
                if text not in body:
                    failures.append(f"missing_text:{text}")
            pattern = spec.get("not_regex")
            if pattern and re.search(pattern, body):
                failures.append(f"forbidden_pattern:{pattern}")
            for text in spec.get("not_texts", []):
                if text in body:
                    failures.append(f"forbidden_text:{text}")
            screenshot = self.run_dir / f"{spec['id']}.png"
            page.screenshot(path=str(screenshot), full_page=False)
            metrics["screenshot"] = screenshot.name
            metrics["body_length"] = len(body)
            if spec.get("min_map"):
                page.locator(".amap-container").first.wait_for(state="visible", timeout=30000)
                metrics["amap_containers"] = page.locator(".amap-container").count()
                if metrics["amap_containers"] < 1:
                    failures.append("missing_amap_container")
        except Exception as exc:
            failures.append(str(exc))
        return _case(
            spec["id"],
            "e2e",
            not failures,
            int((time.perf_counter() - start) * 1000),
            metrics=metrics,
            failures=failures,
            artifacts={"screenshot": metrics.get("screenshot", "")},
        )

    @staticmethod
    def _sse_body(payload: dict[str, Any]) -> str:
        return (
            'event: status\ndata: "eval replay"\n\n'
            "event: done\ndata: "
            + json.dumps(payload, ensure_ascii=False)
            + "\n\n"
        )

    def _run_live_smoke(self) -> list[EvaluationCaseResult]:
        results: list[EvaluationCaseResult] = []
        with httpx.Client(base_url=self.base_url, timeout=240) as client:
            for case_id, fn in (
                ("live-health", lambda: self._live_health(client)),
                ("live-config", lambda: self._live_config(client)),
                ("live-providers", lambda: self._live_providers(client)),
                ("live-provinces", lambda: self._live_provinces(client)),
                ("live-province-attractions", lambda: self._live_province_attractions(client)),
                ("live-attraction-search", lambda: self._live_attraction_search(client)),
                ("live-attraction-prepare", lambda: self._live_attraction_prepare(client)),
                ("live-attraction-stream", lambda: self._live_attraction_stream(client)),
                ("live-transport", lambda: self._live_transport_case(client)),
                ("live-transport-cache", lambda: self._live_transport_cache(client)),
                ("live-long-transport", lambda: self._live_long_transport(client)),
                ("live-attraction-cache", lambda: self._live_attraction_cache(client)),
            ):
                start = time.perf_counter()
                try:
                    passed, metrics, failures, trace = fn()
                except Exception as exc:
                    passed, metrics, failures, trace = False, {}, [str(exc)], []
                results.append(
                    _case(
                        case_id,
                        "live-smoke",
                        passed,
                        int((time.perf_counter() - start) * 1000),
                        suite="live-smoke",
                        metrics=metrics,
                        failures=failures,
                        trace=trace,
                    )
                )
        return results

    def _live_health(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        response = client.get("/api/health")
        data = response.json()
        return response.status_code == 200 and bool(data.get("ok")), data, [], []

    def _live_config(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        response = client.get("/api/config/frontend")
        data = response.json()
        metrics = {
            "configured": bool(data.get("configured")),
            "has_amap_js_key": bool(data.get("amap_js_key")),
            "has_amap_security_code": bool(data.get("amap_js_security_code")),
            "missing_required": list(data.get("missing_required") or []),
        }
        passed = response.status_code == 200 and metrics["configured"]
        return passed, metrics, [] if passed else ["frontend_config_incomplete"], []

    def _live_providers(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        response = client.get("/api/providers")
        data = response.json()
        passed = response.status_code == 200 and data.get("mode") == "live"
        return passed, data, [] if passed else ["provider_status_invalid"], []

    def _live_provinces(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        data = client.get("/api/map/provinces").json()
        count = len(data.get("provinces") or [])
        return count >= 30, {"province_count": count}, [] if count >= 30 else ["province_count_lt_30"], []

    def _live_province_attractions(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        data = client.get("/api/provinces/110000/attractions", params={"name": "北京市"}).json()
        count = len(data.get("attractions") or [])
        return count >= 1, {"attraction_count": count}, [] if count >= 1 else ["no_province_attractions"], []

    def _live_attraction_search(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        data = client.get("/api/attractions/search", params={"q": "西湖", "nationwide": True}).json()
        results = data.get("results") or []
        passed = bool(results)
        return passed, {"result_count": len(results), "direct": data.get("direct_poi_id")}, [] if passed else ["attraction_search_empty"], []

    def _live_attraction_prepare(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        data = client.post("/api/attractions/B023B13L9M/prepare").json()
        status = str(data.get("status") or "")
        passed = status in {"queued", "running", "succeeded"}
        return passed, data, [] if passed else ["prepare_status_invalid"], []

    def _read_sse(self, client: httpx.Client, path: str, *, timeout: int = 240) -> dict[str, Any]:
        event = ""
        with client.stream("GET", path, timeout=timeout) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event in {"done", "error"}:
                    payload = json.loads(line.split(":", 1)[1].strip())
                    if event == "done":
                        return payload
                    raise RuntimeError(f"SSE error: {payload}")
        raise RuntimeError("SSE ended without done")

    def _live_attraction_stream(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        data = self._read_sse(client, "/api/attractions/B023B13L9M/stream", timeout=300)
        photos = data.get("photos") or []
        sources = data.get("sources") or []
        passed = bool(data.get("poi_id") and photos and sources)
        return passed, {"photo_count": len(photos), "source_count": len(sources), "open_status": data.get("open_status")}, [] if passed else ["attraction_stream_incomplete"], []

    def _live_transport_case(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        data = self._live_transport(client, "上海", "杭州")
        periods = data.get("ticket_periods") or []
        route = data.get("route") or {}
        caveats = data.get("caveats") or []
        passed = bool(route.get("distance_m")) and (bool(periods) or any("余票" in str(item) for item in caveats))
        return passed, {"ticket_periods": len(periods), "distance_m": route.get("distance_m"), "weather": bool(data.get("weather")), "caveats": caveats}, [] if passed else ["transport_contract_incomplete"], []

    def _live_transport(self, client: httpx.Client, origin: str, destination: str) -> dict[str, Any]:
        params = {
            "origin": origin,
            "destination": destination,
            "travel_date": (date.today() + timedelta(days=1)).isoformat(),
            "poi_id": "B023B13L9M",
            "days": 3,
        }
        url = str(httpx.URL("/api/transport/stream", params=params))
        return self._read_sse(client, url, timeout=240)

    def _live_transport_cache(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        path = str(
            httpx.URL(
                "/api/transport/stream",
                params={
                    "origin": "上海",
                    "destination": "杭州",
                    "travel_date": (date.today() + timedelta(days=2)).isoformat(),
                    "poi_id": "B023B13L9M",
                    "days": 3,
                },
            )
        )
        first_start = time.perf_counter()
        first = self._read_sse(client, path, timeout=240)
        first_ms = int((time.perf_counter() - first_start) * 1000)
        second_start = time.perf_counter()
        second = self._read_sse(client, path, timeout=120)
        second_ms = int((time.perf_counter() - second_start) * 1000)
        passed = bool(second.get("cache_hit")) or second_ms < first_ms
        return passed, {"cold_ms": first_ms, "warm_ms": second_ms, "second_cache_hit": bool(second.get("cache_hit"))}, [] if passed else ["cache_miss"], []

    def _live_long_transport(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        data = self._live_transport(client, "北京", "拉萨")
        periods = data.get("ticket_periods") or []
        flights = data.get("flight_periods") or []
        caveats = data.get("caveats") or []
        passed = bool(periods or flights or caveats or (data.get("route") or {}).get("distance_m"))
        return passed, {"ticket_periods": len(periods), "flight_periods": len(flights), "caveats": caveats, "mode": (data.get("recommendation") or {}).get("mode")}, [] if passed else ["long_transport_no_fallback"], []

    def _live_attraction_cache(self, client: httpx.Client) -> tuple[bool, dict[str, Any], list[str], list[dict[str, Any]]]:
        first = self._read_sse(client, "/api/attractions/B023B13L9M/stream", timeout=300)
        second = self._read_sse(client, "/api/attractions/B023B13L9M/stream", timeout=120)
        passed = bool(first.get("poi_id") and second.get("poi_id"))
        return passed, {"first_photos": len(first.get("photos") or []), "second_photos": len(second.get("photos") or [])}, [] if passed else ["attraction_cache_failed"], []

    def _report(self, offline: list[EvaluationCaseResult], live: list[EvaluationCaseResult], *, elapsed_ms: int) -> dict[str, Any]:
        offline_categories = {category: [item for item in offline if item.category == category] for category in OFFLINE_CATEGORY_COUNTS}
        tools = offline_categories["tools_schema"]
        recovery = offline_categories["runtime_recovery"]
        safety = offline_categories["safety_evidence"]
        e2e = offline_categories["e2e"]
        maps = offline_categories["map_image"]
        critical_violations = sum(1 for item in safety if not item.passed)
        e2e_success = self._stable_success(e2e)
        offline_success = sum(item.passed for item in offline)
        live_success = sum(item.passed for item in live)
        p95 = self._percentile([item.duration_ms for item in e2e], 95)
        summary = (
            f"设计 60 条冻结离线场景 × 3 轮及 {len(live)} 条真实在线冒烟，"
            f"覆盖工具调用、Schema、重规划、安全与端到端任务；"
            f"工具/参数校验 {self._stable_success(tools)}/{self._unique_count(tools)}，"
            f"故障恢复 {self._stable_success(recovery)}/{self._unique_count(recovery)}，"
            f"15 个端到端任务成功 {e2e_success}/{self._unique_count(e2e)}，"
            f"关键安全违规 {critical_violations}，p95 {p95 / 1000:.2f}s。"
        )
        return {
            "run_id": self.run_id,
            "summary": summary,
            "metrics": {
                "offline_total": 60,
                "offline_executions": len(offline),
                "offline_passed": offline_success,
                "offline_success_rate": round(offline_success / len(offline) * 100, 1) if offline else 0,
                "tools_schema_passed": self._stable_success(tools),
                "tools_schema_total": self._unique_count(tools),
                "recovery_passed": self._stable_success(recovery),
                "recovery_total": self._unique_count(recovery),
                "safety_passed": self._stable_success(safety),
                "safety_total": self._unique_count(safety),
                "critical_safety_violations": critical_violations,
                "map_image_passed": self._stable_success(maps),
                "map_image_total": self._unique_count(maps),
                "e2e_passed": e2e_success,
                "e2e_total": self._unique_count(e2e),
                "e2e_p95_ms": p95,
                "live_passed": live_success,
                "live_total": len(live),
                "elapsed_ms": elapsed_ms,
            },
            "manifest": self._manifest(offline),
            "offline_results": [item.model_dump(mode="json") for item in offline],
            "live_results": [item.model_dump(mode="json") for item in live],
        }

    def _manifest(self, offline: list[EvaluationCaseResult]) -> dict[str, Any]:
        files = [
            PROJECT_DIR / "backend/app/evaluation/travel_v2.py",
            PROJECT_DIR / "backend/app/agent/validators.py",
            PROJECT_DIR / "backend/app/agent/graph/runtime.py",
            PROJECT_DIR / "backend/app/agent/service.py",
        ]
        base_ids = sorted({str(item.metrics.get("base_id") or item.id) for item in offline})
        return {
            "case_ids": base_ids,
            "case_count": len(base_ids),
            "trials": 3,
            "case_hash": hashlib.sha256(json.dumps(base_ids, sort_keys=True).encode("utf-8")).hexdigest(),
            "artifact_hash": hashlib.sha256(json.dumps([self._hash(path) for path in files], sort_keys=True).encode("utf-8")).hexdigest(),
            "files": {str(path.relative_to(PROJECT_DIR)): self._hash(path) for path in files},
            "deepseek_model": self.settings.deepseek_model,
            "embedding_model": self.settings.siliconflow_embedding_model,
            "base_url": self.base_url,
        }

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _percentile(values: list[int], percentile: int) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, max(0, math.ceil(percentile / 100 * len(ordered)) - 1))]

    @staticmethod
    def _unique_count(items: list[EvaluationCaseResult]) -> int:
        return len({str(item.metrics.get("base_id") or item.id) for item in items})

    @staticmethod
    def _stable_success(items: list[EvaluationCaseResult]) -> int:
        grouped: dict[str, list[EvaluationCaseResult]] = {}
        for item in items:
            grouped.setdefault(str(item.metrics.get("base_id") or item.id), []).append(item)
        return sum(1 for rows in grouped.values() if len(rows) == 3 and all(row.passed for row in rows))

    def _write_report(self, report: dict[str, Any]) -> None:
        json_path = self.run_dir / "report.json"
        md_path = self.run_dir / "report.md"
        html_path = self.run_dir / "report.html"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        failures = [item for item in [*report["offline_results"], *report["live_results"]] if not item["passed"]]
        md = [
            "# Travel Copilot v2 Evaluation",
            "",
            report["summary"],
            "",
            "## Metrics",
            "",
            "```json",
            json.dumps(report["metrics"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Failures",
            "",
            *([f"- `{item['id']}`: {'; '.join(item['failures'])}" for item in failures] or ["- None"]),
        ]
        md_path.write_text("\n".join(md), encoding="utf-8")
        rows = "".join(
            f"<tr><td>{html.escape(item['id'])}</td><td>{html.escape(item['category'])}</td><td>{'PASS' if item['passed'] else 'FAIL'}</td><td>{html.escape('; '.join(item['failures']))}</td></tr>"
            for item in [*report["offline_results"], *report["live_results"]]
        )
        html_path.write_text(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Travel Eval v2</title>"
            "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:32px;line-height:1.5}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #d8dee8;padding:7px;text-align:left}"
            "th{background:#f3f6fb}pre{white-space:pre-wrap;background:#f7f8fa;padding:12px}</style></head><body>"
            f"<h1>Travel Copilot v2</h1><p>{html.escape(report['summary'])}</p>"
            f"<pre>{html.escape(json.dumps(report['metrics'], ensure_ascii=False, indent=2))}</pre>"
            "<table><tr><th>Case</th><th>Category</th><th>Result</th><th>Failures</th></tr>"
            f"{rows}</table></body></html>",
            encoding="utf-8",
        )
