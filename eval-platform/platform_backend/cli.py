from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .config import PLATFORM_ROOT, TRAVEL_ROOT
from .core.dataset_schema import contamination_report, validate_dataset
from .core.gate import evaluate_gate, gate_markdown, load_gate_config
from .db import (
    create_run,
    get_cases,
    init_db,
    label_pool_for_run,
    list_runs,
    reattribute_run,
    seed_system_configs,
)
from .adapters.support_online import close_support_executor
from .runner import run_dataset
from .importers import online_support_e2e, rag_ablation, rag_quality, runtime300, support_blind
from .importers.common import ImportResult
from .judge.calibration_run import calibrate, write_calibration_report
from .judge.service import JudgeService


EVALUATIONS_ROOT = TRAVEL_ROOT / "data" / "evaluations"


def _latest(pattern: str) -> Path:
    paths = sorted(EVALUATIONS_ROOT.glob(pattern))
    if not paths:
        raise FileNotFoundError(pattern)
    return paths[-1]


def _import_plan() -> list[tuple[str, Path]]:
    return [
        ("rag_ablation", _latest("rag_ablation/*/report.json")),
        ("rag_quality", _latest("rag_quality/*/report.json")),
        ("runtime300", _latest("runtime300/*/report.json")),
        ("support_blind", _latest("support_blind_v5/*/report.json")),
        ("online_support_e2e", _latest("online_support_e2e/*/report.json")),
    ]


def _run_imports(dry_run: bool) -> list[dict[str, Any]]:
    init_db()
    seed_system_configs()
    results: list[ImportResult] = []
    for family, path in _import_plan():
        report = json.loads(path.read_bytes())
        if family == "rag_ablation":
            modes = tuple(
                mode
                for mode in ("vector", "hybrid", "hybrid_rerank")
                if mode in (report.get("metrics") or {})
            )
            if dry_run:
                results.extend(
                    rag_ablation._run_report_dry_run(path=path, report=report, mode=mode)
                    for mode in modes
                )
            else:
                results.extend(rag_ablation.import_report(path, modes=modes))
        elif family == "rag_quality":
            if dry_run:
                results.append(rag_quality.dry_run(path))
            else:
                results.append(rag_quality.import_report(path))
        elif family == "runtime300":
            if dry_run:
                results.append(runtime300._dry_run(path))
            else:
                results.append(runtime300.import_report(path))
        elif family == "support_blind":
            if dry_run:
                results.append(support_blind._dry_run(path))
            else:
                results.append(support_blind.import_report(path))
        else:
            if dry_run:
                results.append(online_support_e2e.dry_run(path))
            else:
                results.append(online_support_e2e.import_report(path))
    return [item.as_dict() for item in results]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evalctl")
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import", help="Import historical evaluations")
    import_parser.add_argument("--all", action="store_true", required=True)
    import_parser.add_argument("--dry-run", action="store_true")
    calibrate_parser = subparsers.add_parser("calibrate", help="Calibrate Judge against labels")
    calibrate_parser.add_argument("--labels", required=True)
    calibrate_parser.add_argument(
        "--rubric",
        choices=["faithfulness", "citation_accuracy", "answer_relevance"],
        action="append",
    )
    calibrate_parser.add_argument("--limit", type=int, default=0)
    validate_parser = subparsers.add_parser("validate-dataset")
    validate_parser.add_argument("path")
    contamination_parser = subparsers.add_parser("contamination-check")
    contamination_parser.add_argument("--dataset", required=True)
    contamination_parser.add_argument("--threshold", type=float, default=0.15)
    gate_parser = subparsers.add_parser("gate")
    gate_parser.add_argument("--run", required=True)
    gate_parser.add_argument("--baseline", default="")
    gate_parser.add_argument("--config", required=True)
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--run", required=True)
    report_parser.add_argument("--baseline", default="")
    report_parser.add_argument("--config", required=True)
    report_parser.add_argument("--format", choices=["md"], default="md")
    reattribute_parser = subparsers.add_parser("reattribute")
    reattribute_parser.add_argument("--run", required=True)
    reattribute_parser.add_argument("--dry-run", action="store_true")
    pool_parser = subparsers.add_parser("export-label-pool")
    pool_parser.add_argument("--run", required=True)
    pool_parser.add_argument("--n", type=int, default=50)
    pool_parser.add_argument("--stratify", default="difficulty")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--dataset", required=True)
    run_parser.add_argument("--system", required=True)
    run_parser.add_argument("--trials", type=int, default=1)
    run_parser.add_argument("--seed", type=int, default=42)
    run_parser.add_argument("--limit", type=int, default=0)
    run_parser.add_argument("--with-judge", action="store_true")
    run_parser.add_argument("--yes", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "import":
        print(json.dumps(_run_imports(args.dry_run), ensure_ascii=False, indent=2))
        return 0
    if args.command == "calibrate":
        init_db()
        labels_path = Path(args.labels)
        rubrics = args.rubric or [
            "faithfulness",
            "citation_accuracy",
            "answer_relevance",
        ]
        async def run_calibration():
            judge = JudgeService()
            try:
                return [
                    await calibrate(
                        labels_path=labels_path,
                        rubric=rubric,
                        judge=judge,
                        limit=args.limit or None,
                    )
                    for rubric in rubrics
                ]
            finally:
                await judge.aclose()

        results = asyncio.run(run_calibration())
        report_path = PLATFORM_ROOT / "docs" / "judge_calibration.md"
        write_calibration_report(
            results,
            labels_path=labels_path,
            output_path=report_path,
        )
        print(
            json.dumps(
                [
                    {
                        "rubric": result.rubric,
                        "available": result.available,
                        "valid": result.valid,
                        "weighted_kappa": result.weighted_kappa,
                        "error": result.error_text,
                    }
                    for result in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if all(result.available for result in results) else 1
    if args.command == "validate-dataset":
        print(
            json.dumps(
                validate_dataset(Path(args.path)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "contamination-check":
        path = Path(args.dataset)
        if not path.exists():
            normalized_name = args.dataset.replace("-", "_")
            path = PLATFORM_ROOT / "datasets" / f"{normalized_name}.jsonl"
            if not path.exists():
                path = PLATFORM_ROOT / "datasets" / f"{args.dataset}.jsonl"
        source_paths = [
            *sorted((TRAVEL_ROOT / "backend/app/support/knowledge").glob("*.md")),
            *sorted((TRAVEL_ROOT / "backend/app/support/skills").glob("*/SKILL.md")),
            TRAVEL_ROOT / "backend/app/agent/prompts.py",
        ]
        print(
            json.dumps(
                contamination_report(
                    path,
                    source_paths=source_paths,
                    threshold=args.threshold,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command in {"gate", "report"}:
        init_db()
        result = evaluate_gate(
            run_id=args.run,
            baseline_run_id=args.baseline,
            config=load_gate_config(Path(args.config)),
        )
        markdown = gate_markdown(result)
        if args.command == "report":
            print(markdown)
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "reattribute":
        init_db()
        if args.run == "all":
            run_ids = [
                item["id"]
                for item in list_runs(limit=200)
                if item["status"] == "completed"
            ]
        else:
            run_ids = [args.run]
        summaries = [
            reattribute_run(run_id, dry_run=args.dry_run)
            for run_id in run_ids
        ]
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0
    if args.command == "export-label-pool":
        init_db()
        rows = label_pool_for_run(
            args.run,
            limit=args.n,
            stratify=args.stratify,
        )
        output = PLATFORM_ROOT / "labels" / "rag_pool_v2.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
            + ("\n" if rows else ""),
            encoding="utf-8",
        )
        print(json.dumps({"path": str(output), "count": len(rows)}, ensure_ascii=False))
        return 0
    if args.command == "run":
        if os.getenv("EVAL_ALLOW_PAID") != "1" or not args.yes:
            print(json.dumps({
                "started": False,
                "reason": "paid_guard",
                "required": ["EVAL_ALLOW_PAID=1", "--yes"],
            }, ensure_ascii=False, indent=2))
            return 2
        init_db()
        seed_system_configs()
        dataset_path = PLATFORM_ROOT / "datasets" / "online_support_e2e_v1.jsonl"
        manifest_path = PLATFORM_ROOT / "datasets" / "online_support_e2e_manifest.json"
        dataset = seed_dataset_from_jsonl(
            args.dataset,
            dataset_path=dataset_path,
            manifest_path=manifest_path,
        )
        cases = get_cases(dataset["id"])
        if args.limit:
            cases = cases[: args.limit]
        run = create_run(
            dataset_id=dataset["id"],
            name=f"Online Support E2E · {args.trials} trials",
            variant=args.system,
            trials=args.trials,
            seed=args.seed,
            split="all",
        )
        async def execute_online():
            try:
                return await run_dataset(
                    run_id=run["id"],
                    cases=cases,
                    variant=args.system,
                    trials=args.trials,
                    seed=args.seed,
                )
            finally:
                await close_support_executor()

        result = asyncio.run(execute_online())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
