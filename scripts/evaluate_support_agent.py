from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import PROJECT_DIR  # noqa: E402
from backend.app.evaluation.support_blind import SupportBlindRunner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Support Agent frozen blind evaluation")
    parser.add_argument(
        "--cases",
        default="evals/support/support_blind_v5.jsonl",
        help="80-case frozen blind set",
    )
    parser.add_argument(
        "--output-dir",
        default="data/evaluations/support_blind_v5",
        help="report output root",
    )
    args = parser.parse_args()
    cases_path = PROJECT_DIR / args.cases if not Path(args.cases).is_absolute() else Path(args.cases)
    output_root = PROJECT_DIR / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    result = asyncio.run(SupportBlindRunner(cases_path=cases_path, output_root=output_root).run())
    print(result["summary"])
    print(f"report={output_root / result['run_id'] / 'report.html'}")


if __name__ == "__main__":
    main()
