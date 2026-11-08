from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    CORS_ORIGINS,
    DATASET_PATH,
    DATASET_V2_PATH,
    FRONTEND_DIST,
    LABELS_DIR,
    LANGFUSE_EXPORT_ENABLED,
    MANIFEST_PATH,
    MANIFEST_V2_PATH,
    MAX_CONCURRENT_RUNS,
    SEED_RUNS,
    TRAVEL_ROOT,
    ensure_dirs,
)
from .dataset_builder import build_cases
from .core.failure_taxonomy import FailureCause
from .db import (
    compare_runs,
    create_run,
    database_health,
    diff_dataset_versions,
    failure_clusters,
    failure_review_consistency,
    flaky_cases,
    get_case_result,
    get_cases,
    get_dataset,
    get_run,
    get_trace,
    init_db,
    interrupt_incomplete_runs,
    labeling_candidates,
    list_case_results,
    list_datasets,
    list_human_labels,
    list_runs,
    list_system_configs,
    seed_dataset_from_jsonl,
    seed_system_configs,
    trend_runs,
    upsert_human_label,
    update_run_status,
)
from .runner import run_dataset


DATASET_ID = "traveling-business-v2"
V1_DATASET_ID = "traveling-business-v1"
logger = logging.getLogger(__name__)


def _ensure_dataset() -> None:
    ensure_dirs()
    if DATASET_PATH.exists() and MANIFEST_PATH.exists():
        return
    cases = build_cases()
    DATASET_PATH.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "name": "Traveling 业务评测回归集",
                "version": "1.0.0",
                "description": "覆盖交通策略、景区证据、Agent 契约、Supervisor 路由、知识检索和安全边界的 60 条可控业务回归场景。",
                "case_count": len(cases),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


async def _seed_initial_runs() -> None:
    if not SEED_RUNS:
        return
    existing = list_runs(limit=200)
    if any(
        run["dataset_id"] == DATASET_ID and run["status"] == "completed"
        for run in existing
    ):
        return
    cases = get_cases(DATASET_ID)
    no_rag = create_run(
        dataset_id=DATASET_ID,
        name="Baseline · 无 RAG",
        variant="baseline-no-rag",
        system_config_id="baseline-no-rag",
    )
    await run_dataset(run_id=no_rag["id"], cases=cases, variant="baseline-no-rag")
    basic = create_run(
        dataset_id=DATASET_ID,
        name="Baseline · 基础校验",
        variant="baseline-basic-validators",
        system_config_id="baseline-basic-validators",
    )
    await run_dataset(run_id=basic["id"], cases=cases, variant="baseline-basic-validators")
    candidate = create_run(
        dataset_id=DATASET_ID,
        name="Candidate · 当前配置",
        variant="candidate-current",
        system_config_id="candidate-current",
        baseline_run_id=no_rag["id"],
    )
    await run_dataset(run_id=candidate["id"], cases=cases, variant="candidate-current")


def _schedule_run(
    *,
    cases: list[dict[str, Any]],
    run_id: str,
    variant: str,
    trials: int,
    seed: int,
) -> asyncio.Task:
    async def execute() -> None:
        async with app.state.run_semaphore:
            await run_dataset(
                run_id=run_id,
                cases=cases,
                variant=variant,
                trials=trials,
                seed=seed,
            )

    task = asyncio.create_task(execute(), name=f"eval-run:{run_id}")
    app.state.tasks.add(task)

    def done(completed: asyncio.Task) -> None:
        app.state.tasks.discard(completed)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.error(
                "Evaluation run failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(done)
    return task


@asynccontextmanager
async def lifespan(_: FastAPI):
    _ensure_dataset()
    init_db()
    seed_system_configs()
    seed_dataset_from_jsonl(
        V1_DATASET_ID,
        dataset_path=DATASET_PATH,
        manifest_path=MANIFEST_PATH,
    )
    seed_dataset_from_jsonl(
        DATASET_ID,
        dataset_path=DATASET_V2_PATH,
        manifest_path=MANIFEST_V2_PATH,
    )
    interrupt_incomplete_runs()
    app.state.tasks = set()
    app.state.run_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    await _seed_initial_runs()
    yield
    pending = list(app.state.tasks)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(
    title="Traveling Business Eval Platform",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunCreate(BaseModel):
    dataset_id: str = DATASET_ID
    name: str = Field(default="Candidate evaluation", min_length=1, max_length=120)
    variant: Literal[
        "baseline-no-rag",
        "baseline-basic-validators",
        "candidate-current",
    ] = "candidate-current"
    baseline_run_id: str | None = None
    trials: int = Field(default=1, ge=1, le=10)
    seed: int = Field(default=42, ge=0)
    split: Literal["all", "dev", "holdout"] = "all"


class HumanLabelCreate(BaseModel):
    run_id: str
    case_key: str
    dimension: Literal[
        "faithfulness",
        "citation_accuracy",
        "answer_relevance",
        "failure_attribution",
    ]
    rubric_version: str = "1.0"
    score: int = Field(ge=1, le=5)
    rationale: str = Field(default="", max_length=1000)
    labeler: str = Field(default="owner", min_length=1, max_length=80)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    database_ok = database_health()
    return {
        "ok": database_ok,
        "database": "ok" if database_ok else "error",
        "langfuse_export": "enabled" if LANGFUSE_EXPORT_ENABLED else "disabled",
        "travel_root": str(TRAVEL_ROOT),
        "frontend_built": FRONTEND_DIST.exists(),
    }


@app.get("/api/overview")
async def overview() -> dict[str, Any]:
    datasets = list_datasets()
    runs = list_runs(limit=20)
    latest = next((run for run in runs if run["status"] == "completed"), None)
    imported_runs = [
        run for run in list_runs(limit=200) if run.get("kind") == "imported"
    ]
    imported = imported_runs[0] if imported_runs else None
    return {
        "datasets": len(datasets),
        "runs": len(runs),
        "completed_runs": sum(1 for run in runs if run["status"] == "completed"),
        "latest_metrics": latest["metrics"] if latest else {},
        "latest_run": latest,
        "latest_imported_run": imported,
        "imported_runs": imported_runs,
        "langfuse_export": "enabled" if LANGFUSE_EXPORT_ENABLED else "disabled",
    }


@app.get("/api/datasets")
async def datasets() -> dict[str, Any]:
    return {"items": list_datasets()}


@app.get("/api/system-configs")
async def system_configs() -> dict[str, Any]:
    return {"items": list_system_configs()}


@app.get("/api/labeling/items")
async def labeling_items(
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return {"items": labeling_candidates(limit)}


@app.post("/api/labels")
async def create_label(payload: HumanLabelCreate) -> dict[str, Any]:
    if get_run(payload.run_id) is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return upsert_human_label(
        run_id=payload.run_id,
        case_key=payload.case_key,
        dimension=payload.dimension,
        rubric_version=payload.rubric_version,
        score=payload.score,
        rationale=payload.rationale,
        labeler=payload.labeler,
    )


@app.get("/api/labels")
async def labels(run_id: str = "", labeler: str = "") -> dict[str, Any]:
    return {"items": list_human_labels(run_id=run_id, labeler=labeler)}


@app.post("/api/labels/export")
async def export_labels(run_id: str = "", labeler: str = "") -> dict[str, Any]:
    ensure_dirs()
    rows = list_human_labels(run_id=run_id, labeler=labeler)
    path = LABELS_DIR / "rag_gold_v1.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return {"path": str(path), "count": len(rows)}


@app.get("/api/datasets/{dataset_id}")
async def dataset_detail(dataset_id: str) -> dict[str, Any]:
    dataset = get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset_not_found")
    return dataset


@app.get("/api/datasets/{dataset_id}/cases")
async def dataset_cases(dataset_id: str) -> dict[str, Any]:
    if get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset_not_found")
    return {"items": get_cases(dataset_id)}


@app.get("/api/datasets/{dataset_id}/diff")
async def dataset_diff(
    dataset_id: str,
    from_version: str,
    to_version: str,
) -> dict[str, Any]:
    if get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset_not_found")
    return diff_dataset_versions(dataset_id, from_version, to_version)


@app.get("/api/runs")
async def runs(limit: int = Query(default=100, ge=1, le=200)) -> dict[str, Any]:
    return {"items": list_runs(limit=limit)}


@app.post("/api/runs")
async def start_run(payload: RunCreate) -> dict[str, Any]:
    dataset = get_dataset(payload.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset_not_found")
    if payload.baseline_run_id:
        baseline = get_run(payload.baseline_run_id)
        if baseline is None or baseline["dataset_id"] != payload.dataset_id:
            raise HTTPException(status_code=422, detail="invalid_baseline_run_id")
    run = create_run(
        dataset_id=payload.dataset_id,
        name=payload.name,
        variant=payload.variant,
        baseline_run_id=payload.baseline_run_id,
        config={"trigger": "ui"},
        seed=payload.seed,
        trials=payload.trials,
        split=payload.split,
    )
    cases = get_cases(payload.dataset_id)
    if payload.split != "all":
        cases = [case for case in cases if case.get("split") == payload.split]
    _schedule_run(
        cases=cases,
        run_id=run["id"],
        variant=payload.variant,
        trials=payload.trials,
        seed=payload.seed,
    )
    return run


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    if run["status"] not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="run_not_cancellable")
    for task in app.state.tasks:
        if task.get_name() == f"eval-run:{run_id}":
            task.cancel()
            break
    update_run_status(run_id, "cancelled", error="cancelled_by_user")
    return get_run(run_id) or run


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return run


@app.get("/api/runs/{run_id}/cases")
async def run_cases(
    run_id: str,
    status: str = "",
    category: str = "",
) -> dict[str, Any]:
    if get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return {"items": list_case_results(run_id, status=status, category=category)}


@app.get("/api/runs/{run_id}/flaky")
async def flaky_run_cases(run_id: str) -> dict[str, Any]:
    if get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return {"items": flaky_cases(run_id)}


@app.get("/api/runs/{run_id}/failure-clusters")
async def run_failure_clusters(run_id: str) -> dict[str, Any]:
    if get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return {
        "items": failure_clusters(run_id),
        "review": failure_review_consistency(run_id),
    }


@app.post("/api/runs/{run_id}/cases/{case_key}/failure-review")
async def review_failure(
    run_id: str,
    case_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if get_run(run_id) is None or get_case_result(run_id, case_key) is None:
        raise HTTPException(status_code=404, detail="case_not_found")
    score = int(payload.get("score") or 0)
    if score not in {1, 2, 3}:
        raise HTTPException(status_code=422, detail="invalid_failure_review_score")
    corrected_cause = ""
    if score == 2:
        corrected_cause = str(payload.get("corrected_cause") or "")
        if corrected_cause not in {item.value for item in FailureCause}:
            raise HTTPException(status_code=422, detail="corrected_cause_required")
    return upsert_human_label(
        run_id=run_id,
        case_key=case_key,
        dimension="failure_attribution",
        rubric_version="failure-review-v1",
        score=score,
        rationale=str(payload.get("rationale") or "")[:1000],
        labeler=str(payload.get("labeler") or "owner")[:80],
        corrected_cause=corrected_cause,
    )


@app.get("/api/runs/{run_id}/cases/{case_key}/trace")
async def trace_detail(run_id: str, case_key: str) -> dict[str, Any]:
    if get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    result = get_case_result(run_id, case_key)
    if result is None:
        raise HTTPException(status_code=404, detail="case_not_found")
    return {"case": result, "spans": get_trace(run_id, case_key)}


@app.get("/api/compare")
async def compare(
    base_run_id: str,
    candidate_run_id: str,
) -> dict[str, Any]:
    try:
        return compare_runs(base_run_id, candidate_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/trends")
async def trends(
    dataset_id: str,
    system_config_id: str = "",
    limit: int = Query(default=50, ge=2, le=200),
) -> dict[str, Any]:
    return {
        "items": trend_runs(
            dataset_id=dataset_id,
            system_config_id=system_config_id,
            limit=limit,
        )
    }


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="eval-assets")

    @app.get("/", include_in_schema=False)
    async def frontend_root() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def frontend_fallback(full_path: str) -> FileResponse | JSONResponse:
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "not_found"})
        candidate = (FRONTEND_DIST / full_path).resolve()
        if candidate.is_relative_to(FRONTEND_DIST.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
