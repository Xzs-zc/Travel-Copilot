from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import PROJECT_DIR  # noqa: E402
from backend.app.evaluation.rag_quality import RSIConfig, RAGQualityRunner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG quality evaluation with MiMo LLM-as-a-Judge")
    parser.add_argument("--cases", default="evals/runtime300/rag_100.jsonl")
    parser.add_argument("--output-dir", default="data/evaluations/rag_quality")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--expected-cases", type=int, default=100)
    parser.add_argument("--no-rsi", action="store_true")
    parser.add_argument("--max-planning-rounds", type=int, default=2)
    parser.add_argument("--time-budget-seconds", type=int, default=180)
    args = parser.parse_args()
    cases_path = PROJECT_DIR / args.cases if not Path(args.cases).is_absolute() else Path(args.cases)
    output_root = PROJECT_DIR / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    report = asyncio.run(
        RAGQualityRunner(
            cases_path=cases_path,
            output_root=output_root,
            concurrency=args.concurrency,
            expected_cases=args.expected_cases,
            rsi_enabled=not args.no_rsi,
            rsi_config=RSIConfig(
                max_planning_rounds=args.max_planning_rounds,
                max_total_answer_attempts=args.max_planning_rounds + 1,
                max_judge_calls=args.max_planning_rounds + 1,
                max_queries=3,
                time_budget_seconds=args.time_budget_seconds,
            ),
        ).run()
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"report={output_root / report['run_id'] / 'report.html'}")


if __name__ == "__main__":
    main()
