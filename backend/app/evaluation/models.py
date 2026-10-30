from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvaluationCaseResult(BaseModel):
    id: str
    name: str
    category: str
    suite: Literal["offline", "live-smoke"]
    hard_gate: bool = False
    passed: bool
    score: float = 0
    duration_ms: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    root_causes: list[str] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    run_id: str
    suite: str
    variant: str = "full"
    trials: int = 1
    started_at: datetime
    finished_at: datetime = Field(default_factory=datetime.now)
    base_url: str = ""
    passed: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    baseline_delta: dict[str, Any] = Field(default_factory=dict)
    trial_metrics: dict[str, Any] = Field(default_factory=dict)
    ablation: dict[str, Any] = Field(default_factory=dict)
    isolation: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(default_factory=dict)
    cases: list[EvaluationCaseResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
