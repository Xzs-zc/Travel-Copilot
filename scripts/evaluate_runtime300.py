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
from backend.app.evaluation.runtime300 import Runtime300Runner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Travel Copilot 300-case runtime evaluation")
    parser.add_argument("--cases-dir", default="evals/runtime300")
    parser.add_argument("--output-dir", default="data/evaluations/runtime300")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    cases_dir = PROJECT_DIR / args.cases_dir if not Path(args.cases_dir).is_absolute() else Path(args.cases_dir)
    output_root = PROJECT_DIR / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    report = asyncio.run(
        Runtime300Runner(
            cases_dir=cases_dir,
            output_root=output_root,
            base_url=args.base_url,
        ).run()
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(report["summary"])
    print(f"report={output_root / report['run_id'] / 'report.html'}")


if __name__ == "__main__":
    main()
