from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schemas import SourceRef


class EvidenceItem(BaseModel):
    id: str
    field: str
    value: Any = None
    source: SourceRef | None = None
    confidence: float = 1.0
    status: Literal["verified", "partial", "unknown", "conflict"] = "verified"
    note: str = ""


class PlanStep(BaseModel):
    step: int
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["planned", "running", "succeeded", "failed", "skipped"] = "planned"
    attempts: int = 0
    duration_ms: int = 0
    error: str = ""


class ReplanDelta(BaseModel):
    changed_fields: list[str] = Field(default_factory=list)
    invalidated_evidence: list[str] = Field(default_factory=list)
    reused_evidence: list[str] = Field(default_factory=list)


class TaskState(BaseModel):
    run_id: str
    task: str
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    replan_count: int = 0
    max_replans: int = 2
    tool_calls: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    status: Literal["planned", "running", "succeeded", "degraded", "failed"] = "planned"
    updated_at: datetime = Field(default_factory=datetime.now)

    def add_evidence(self, items: list[EvidenceItem]) -> None:
        existing = {item.id for item in self.evidence}
        for item in items:
            if item.id in existing:
                continue
            existing.add(item.id)
            self.evidence.append(item)
        self.updated_at = datetime.now()

    def add_tokens(self, usage: dict[str, int] | None) -> None:
        for key, value in (usage or {}).items():
            if isinstance(value, int):
                self.token_usage[key] = self.token_usage.get(key, 0) + value


def build_replan_delta(task: str, previous: dict[str, Any] | None, current: dict[str, Any]) -> ReplanDelta:
    previous = previous or {}
    changed = [key for key in set(previous) | set(current) if previous.get(key) != current.get(key)]
    invalidated: list[str] = []
    reused: list[str] = []
    if task == "transport_plan":
        if {"origin", "destination"} & set(changed):
            invalidated.extend(["transport.route", "transport.ticket_periods", "weather.forecast"])
        elif "travel_date" in changed:
            invalidated.extend(["transport.ticket_periods", "weather.forecast"])
        elif "days" in changed:
            invalidated.append("weather.forecast")
        elif {"preferred_transport", "travel_style", "avoid"} & set(changed):
            invalidated.extend(["transport.ticket_periods", "transport.route"])
        reused = [item for item in ("transport.route", "weather.forecast", "transport.ticket_periods") if item not in invalidated]
    elif task == "attraction_page" and "poi_id" in changed:
        invalidated.append("attraction.*")
    return ReplanDelta(changed_fields=sorted(changed), invalidated_evidence=sorted(set(invalidated)), reused_evidence=sorted(set(reused)))


def detect_evidence_conflicts(items: list[EvidenceItem]) -> list[EvidenceItem]:
    grouped: dict[str, list[EvidenceItem]] = {}
    for item in items:
        grouped.setdefault(item.field, []).append(item)
    conflicts: list[EvidenceItem] = []
    for group in grouped.values():
        if len({str(item.value) for item in group}) < 2:
            continue
        providers = {item.source.provider for item in group if item.source}
        if len(providers) < 2:
            continue
        for item in group:
            item.status = "conflict"
            conflicts.append(item)
    return conflicts


def preferred_evidence(items: list[EvidenceItem], field: str, preferred_providers: tuple[str, ...] = ("官方网站", "高德地图")) -> EvidenceItem | None:
    candidates = [item for item in items if item.field == field]
    for provider in preferred_providers:
        match = next((item for item in candidates if item.source and item.source.provider == provider), None)
        if match:
            return match
    return candidates[0] if candidates else None


def build_task_state(
    *,
    run_id: str,
    task: str,
    goal: str,
    constraints: dict[str, Any] | None = None,
) -> TaskState:
    values = dict(constraints or {})
    required_by_task = {
        "transport_plan": ("origin", "destination", "travel_date"),
        "attraction_page": ("poi_id",),
    }
    missing = [key for key in required_by_task.get(task, ()) if not values.get(key)]
    plan_by_task = {
        "transport_plan": [
            PlanStep(step=1, tool="get_preferences"),
            PlanStep(step=2, tool="query_train"),
            PlanStep(step=3, tool="route_estimate"),
            PlanStep(step=4, tool="get_weather"),
            PlanStep(step=5, tool="submit_transport_plan"),
        ],
        "attraction_page": [
            PlanStep(step=1, tool="get_attraction_detail"),
            PlanStep(step=2, tool="verify_official_page"),
            PlanStep(step=3, tool="get_weather"),
            PlanStep(step=4, tool="submit_attraction_page"),
        ],
    }
    return TaskState(
        run_id=run_id,
        task=task,
        goal=goal,
        constraints=values,
        missing_fields=missing,
        plan=plan_by_task.get(task, []),
    )
