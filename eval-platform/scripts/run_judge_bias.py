from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import db
from platform_backend.judge.bias import length_bias, position_bias, self_consistency
from platform_backend.judge.service import JudgeService


async def main(limit: int) -> None:
    db.init_db()
    candidates = db.labeling_candidates(limit=100)
    samples = candidates[:limit]
    if not samples:
        raise RuntimeError("no_answer_bearing_rag_samples")
    judge = JudgeService()

    async def score_once(sample: dict) -> float:
        result = await judge.call(
            rubric_name="answer_relevance",
            sample={"question": sample["question"]},
            output={"answer": sample["answer"]},
            use_cache=False,
            persist=False,
        )
        if result.status != "ok":
            raise RuntimeError(result.error_text or "judge_error")
        return float(result.result.get("score") or 0)

    async def compare(left: dict, right: dict) -> int:
        return await judge.compare_pair(
            question=left["question"],
            left=left["answer"],
            right=right["answer"],
        )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in samples:
        grouped[item["question"]].append(item)
    pairs = [
        (items[0], items[1])
        for items in grouped.values()
        if len(items) >= 2 and items[0]["answer"] != items[1]["answer"]
    ][: max(1, limit // 2)]

    results = {}
    results["self_consistency"] = await self_consistency(
        judge_model=judge.model,
        samples=samples,
        score_once=score_once,
        repeats=3,
    )
    if pairs:
        results["position"] = await position_bias(
            judge_model=judge.model,
            pairs=[(left, right) for left, right in pairs],
            compare=compare,
        )
    scores = [await score_once(item) for item in samples]
    results["length"] = length_bias(
        [len(item["answer"]) for item in samples],
        scores,
        judge_model=judge.model,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(max(1, min(args.limit, 20))))
