from __future__ import annotations

import hashlib
import json
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
TRAVEL_ROOT = PLATFORM_ROOT.parent
SOURCE_PATH = TRAVEL_ROOT / "evals" / "support" / "support_blind_v5.jsonl"
DATASET_PATH = PLATFORM_ROOT / "datasets" / "online_support_e2e_v1.jsonl"
MANIFEST_PATH = PLATFORM_ROOT / "datasets" / "online_support_e2e_manifest.json"


def main() -> None:
    source_rows = [
        json.loads(line)
        for line in SOURCE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    e2e_rows = [row for row in source_rows if row.get("block") == "e2e"]
    dataset_rows = [
        {
            "case_key": row["id"],
            "name": f"Online support {row['id']}",
            "category": "online_e2e",
            "severity": "high",
            "difficulty": row.get("difficulty") or "medium",
            "input": {
                "type": "support_e2e",
                "message": row["message"],
                "context": row.get("context") or {},
                "expected": row["expected"],
            },
            "expected": row["expected"],
            "tags": ["online", "support_e2e", "adapted"],
            "source": "adapted",
            "rationale": "来自 support_blind_v5 E2E，用于真实在线多 trial 评测。",
            "split": "all",
        }
        for row in e2e_rows
    ]
    DATASET_PATH.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in dataset_rows
        )
        + "\n",
        encoding="utf-8",
    )
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "name": "Online Support E2E v1",
                "version": "1.0.0",
                "description": "20 条真实客服 E2E，独立于离线 v2 数据集。",
                "case_count": len(dataset_rows),
                "source": "evals/support/support_blind_v5.jsonl",
                "source_sha256": hashlib.sha256(
                    SOURCE_PATH.read_bytes()
                ).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "dataset": str(DATASET_PATH),
                "case_count": len(dataset_rows),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
