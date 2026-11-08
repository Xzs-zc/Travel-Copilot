from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.config import DATASET_V2_PATH, MANIFEST_V2_PATH  # noqa: E402
from platform_backend.core.gate import (  # noqa: E402
    evaluate_gate,
    gate_markdown,
    load_gate_config,
)
from platform_backend.db import (  # noqa: E402
    create_run,
    get_cases,
    init_db,
    seed_dataset_from_jsonl,
    seed_system_configs,
)
from platform_backend.runner import run_dataset  # noqa: E402


DATASET_ID = "traveling-business-v2"


async def main() -> int:
    init_db()
    seed_system_configs()
    seed_dataset_from_jsonl(
        DATASET_ID,
        dataset_path=DATASET_V2_PATH,
        manifest_path=MANIFEST_V2_PATH,
    )
    cases = [
        case
        for case in get_cases(DATASET_ID)
        if case.get("split") == "dev"
    ]
    baseline = create_run(
        dataset_id=DATASET_ID,
        name="Gate baseline · no RAG",
        variant="baseline-no-rag",
        split="dev",
    )
    await run_dataset(
        run_id=baseline["id"],
        cases=cases,
        variant="baseline-no-rag",
    )
    candidate = create_run(
        dataset_id=DATASET_ID,
        name="Gate candidate · current",
        variant="candidate-current",
        baseline_run_id=baseline["id"],
        split="dev",
    )
    await run_dataset(
        run_id=candidate["id"],
        cases=cases,
        variant="candidate-current",
    )
    result = evaluate_gate(
        run_id=candidate["id"],
        baseline_run_id=baseline["id"],
        config=load_gate_config(PLATFORM_ROOT / "configs" / "gate.yaml"),
    )
    report_path = PLATFORM_ROOT / "reports" / "gate-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(gate_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
