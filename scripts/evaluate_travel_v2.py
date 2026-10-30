from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import PROJECT_DIR  # noqa: E402
from backend.app.evaluation.travel_v2 import TravelEvalV2Runner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Travel Copilot v2 evaluation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output-dir", default="data/evaluations/travel_v2")
    args = parser.parse_args()
    output_root = PROJECT_DIR / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    report = TravelEvalV2Runner(base_url=args.base_url, output_root=output_root).run()
    print(report["summary"])
    print(f"report={output_root / report['run_id'] / 'report.html'}")


if __name__ == "__main__":
    main()
