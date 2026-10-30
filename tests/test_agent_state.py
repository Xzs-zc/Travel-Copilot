import pytest

from backend.app.agent.state import EvidenceItem, build_replan_delta, build_task_state
from backend.app.agent.tools import ToolRegistry
from backend.app.agent.validators import validate_arguments, validate_final_result, validate_tool_result
from backend.app.providers.official import OfficialPageProvider
from backend.app.providers.rail import Train12306Client


class FlakyAmap:
    def __init__(self):
        self.calls = 0

    async def weather(self, city):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary outage")
        return {"city": city, "casts": [{"date": "2026-10-01", "dayweather": "晴"}]}


def test_task_state_contains_plan_constraints_and_missing_fields():
    state = build_task_state(
        run_id="run-1",
        task="transport_plan",
        goal="上海到杭州",
        constraints={"origin": "上海", "destination": "杭州"},
    )
    assert state.missing_fields == ["travel_date"]
    assert [step.tool for step in state.plan][:3] == ["get_preferences", "query_train", "route_estimate"]


def test_validators_reject_invalid_arguments_and_unsafe_final_result():
    normalized, errors = validate_arguments("query_train", {"origin": "上海", "destination": "杭州", "travel_date": "bad"})
    assert normalized is None
    assert errors

    result = validate_final_result("submit_transport_plan", {"blessing": "愿君平安。", "caveats": ["G123 有余票"]})
    assert not result.ok
    assert "train_code_leak" in result.errors


def test_tool_result_validation_builds_evidence():
    result = validate_tool_result(
        "route_estimate",
        {
            "ok": True,
            "route": {
                "origin_location": "121,31",
                "destination_location": "120,30",
                "distance_m": 177000,
                "source": {"title": "高德驾车路线", "provider": "高德地图"},
            },
        },
        {"origin": "上海", "destination": "杭州"},
    )
    assert result.ok
    assert result.evidence[0].field == "transport.route"


def test_evidence_ledger_deduplicates():
    state = build_task_state(run_id="run-2", task="transport_plan", goal="上海到杭州", constraints={"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"})
    item = EvidenceItem(id="prefs", field="preferences", value={})
    state.add_evidence([item, item])
    assert len(state.evidence) == 1


@pytest.mark.asyncio
async def test_tool_registry_retries_transient_failure():
    amap = FlakyAmap()
    registry = ToolRegistry(amap=amap, rail=Train12306Client(), official=OfficialPageProvider(), preferences={})
    result = await registry.execute("get_weather", {"city": "杭州"})
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert amap.calls == 2


def test_public_landing_delta_reuses_route_but_refreshes_tickets_and_weather():
    delta = build_replan_delta(
        "transport_plan",
        {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01", "days": 3},
        {"origin": "上海", "destination": "杭州", "travel_date": "2026-10-02", "days": 3},
    )
    assert delta.changed_fields == ["travel_date"]
    assert "transport.ticket_periods" in delta.invalidated_evidence
    assert "weather.forecast" in delta.invalidated_evidence
    assert "transport.route" in delta.reused_evidence


def test_empty_train_result_is_a_planning_signal_not_a_validation_error():
    result = validate_tool_result(
        "query_train",
        {"ok": True, "no_service": True, "ticket_periods": [], "train_summary": {"train_count": 0}},
        {"origin": "梁山", "destination": "林芝", "travel_date": "2026-10-08"},
    )
    assert result.ok is True
    assert result.errors == []
