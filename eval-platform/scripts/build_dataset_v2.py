from __future__ import annotations

import json
import sys
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
TRAVEL_ROOT = PLATFORM_ROOT.parent
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.dataset_builder_v2 import build_v2_cases, distribution  # noqa: E402


def main() -> None:
    v1_path = PLATFORM_ROOT / "datasets" / "traveling_business_v1.jsonl"
    rag_path = (
        TRAVEL_ROOT
        / "data"
        / "evaluations"
        / "rag_ablation"
        / "20261105-055721"
        / "report.json"
    )
    v1_rows = [
        json.loads(line)
        for line in v1_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rag_report = json.loads(rag_path.read_text(encoding="utf-8"))
    cases = build_v2_cases(v1_rows=v1_rows, rag_report=rag_report)
    output_path = PLATFORM_ROOT / "datasets" / "traveling_business_v2.jsonl"
    output_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "Traveling 业务评测回归集 v2",
        "version": "2.0.0",
        "description": "覆盖交通、契约、路由、安全、检索与轨迹审计的分层回归集。",
        "case_count": len(cases),
        **distribution(cases),
    }
    (PLATFORM_ROOT / "datasets" / "manifest_v2.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
