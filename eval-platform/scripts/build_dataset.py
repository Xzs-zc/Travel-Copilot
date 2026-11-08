from __future__ import annotations

import json
import sys
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.dataset_builder import build_cases  # noqa: E402


def main() -> None:
    dataset_dir = PLATFORM_ROOT / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    dataset_path = dataset_dir / "traveling_business_v1.jsonl"
    dataset_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "Traveling 业务评测回归集",
        "version": "1.0.0",
        "description": "覆盖交通策略、景区证据、Agent 契约、Supervisor 路由、知识检索和安全边界的 60 条可控业务回归场景。",
        "case_count": len(cases),
        "categories": {
            category: sum(1 for case in cases if case["category"] == category)
            for category in sorted({case["category"] for case in cases})
        },
    }
    (dataset_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} cases to {dataset_path}")


if __name__ == "__main__":
    main()
