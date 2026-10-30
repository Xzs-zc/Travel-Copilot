from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from .report import compare_baseline, write_report
from .runner import PROJECT_DIR, EvaluationRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Travel Copilot Agent evaluation")
    parser.add_argument("--suite", choices=("offline", "live-smoke", "all"), default="offline")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output-dir", default="data/evaluations")
    parser.add_argument("--baseline", default="data/evaluations/baseline.json")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--trials", type=int, default=3, help="每个场景独立执行次数，默认 3")
    parser.add_argument("--variant", default="full", help="评测变体：full/no_llm/no_vision/no_replan/no_cache")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = (PROJECT_DIR / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    run_dir = output_root / timestamp
    runner = EvaluationRunner(base_url=args.base_url, run_dir=run_dir, trials=max(1, args.trials), variant=args.variant)
    report = runner.run(args.suite)
    baseline_path = (PROJECT_DIR / args.baseline).resolve() if not Path(args.baseline).is_absolute() else Path(args.baseline)
    if baseline_path.exists() and not args.save_baseline:
        try:
            report.baseline_delta = compare_baseline(report, json.loads(baseline_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    json_path, markdown_path, html_path = write_report(report, run_dir)
    if args.save_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(json_path, baseline_path)
    print(f"passed={report.passed} score={report.metrics.get('task_success_rate', 0)}%")
    print(f"html={html_path}")
    print(f"json={json_path}")
    print(f"markdown={markdown_path}")
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
