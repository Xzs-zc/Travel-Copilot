from __future__ import annotations

from itertools import combinations
from typing import Any, Awaitable, Callable

from .. import db


Completion = Callable[[str], Awaitable[dict[str, Any]]]


async def position_bias(
    *,
    judge_model: str,
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    compare: Callable[[dict[str, Any], dict[str, Any]], Awaitable[int]],
) -> dict[str, float]:
    flips = 0
    for left, right in pairs:
        forward = await compare(left, right)
        reversed_order = await compare(right, left)
        if forward != -reversed_order:
            flips += 1
    rate = flips / len(pairs) if pairs else 0.0
    metrics = {"sample_count": len(pairs), "flip_count": flips, "flip_rate": round(rate, 6)}
    db.save_bias_result(
        check_type="position",
        judge_model=judge_model,
        sample_count=len(pairs),
        metrics=metrics,
    )
    return metrics


async def self_consistency(
    *,
    judge_model: str,
    samples: list[dict[str, Any]],
    score_once: Callable[[dict[str, Any]], Awaitable[float]],
    repeats: int = 3,
) -> dict[str, float]:
    variances: list[float] = []
    for sample in samples:
        scores = [await score_once(sample) for _ in range(repeats)]
        mean = sum(scores) / len(scores)
        variances.append(sum((score - mean) ** 2 for score in scores) / len(scores))
    metrics = {
        "sample_count": len(samples),
        "repeats": repeats,
        "mean_variance": round(sum(variances) / len(variances), 6) if variances else 0.0,
    }
    db.save_bias_result(
        check_type="self_consistency",
        judge_model=judge_model,
        sample_count=len(samples),
        metrics=metrics,
    )
    return metrics


def spearman(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda index: values[index])
        result = [0.0] * len(values)
        index = 0
        while index < len(order):
            end = index + 1
            while end < len(order) and values[order[end]] == values[order[index]]:
                end += 1
            rank = (index + end - 1) / 2 + 1
            for position in order[index:end]:
                result[position] = rank
            index = end
        return result

    left_rank = ranks(left)
    right_rank = ranks(right)
    left_mean = sum(left_rank) / len(left_rank)
    right_mean = sum(right_rank) / len(right_rank)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_rank, right_rank, strict=True)
    )
    denominator = (
        sum((a - left_mean) ** 2 for a in left_rank)
        * sum((b - right_mean) ** 2 for b in right_rank)
    ) ** 0.5
    return round(numerator / denominator, 6) if denominator else 0.0


def length_bias(lengths: list[float], scores: list[float], *, judge_model: str) -> dict[str, float]:
    metric = spearman(lengths, scores)
    metrics = {"sample_count": len(lengths), "spearman": metric}
    db.save_bias_result(
        check_type="length",
        judge_model=judge_model,
        sample_count=len(lengths),
        metrics=metrics,
    )
    return metrics
