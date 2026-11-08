from __future__ import annotations

from collections import Counter
from typing import Any


def cohen_kappa(left: list[int], right: list[int]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("label_vectors_must_match_and_be_non_empty")
    total = len(left)
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / total
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[label] / total) * (right_counts[label] / total)
        for label in set(left_counts) | set(right_counts)
    )
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return round((observed - expected) / (1 - expected), 6)


def weighted_kappa(left: list[int], right: list[int]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("label_vectors_must_match_and_be_non_empty")
    total = len(left)
    min_score = min(left + right)
    max_score = max(left + right)
    span = max(1, max_score - min_score)

    def distance(a: int, b: int) -> float:
        return ((a - b) / span) ** 2

    observed = sum(distance(a, b) for a, b in zip(left, right, strict=True)) / total
    expected = sum(
        distance(a, b) * left.count(a) * right.count(b)
        for a in set(left)
        for b in set(right)
    ) / (total * total)
    if expected == 0:
        return 1.0 if observed == 0 else 0.0
    return round(1 - observed / expected, 6)


def confusion_matrix(labels: list[int]) -> dict[str, int]:
    return {f"{a}->{b}": count for (a, b), count in sorted(Counter(labels).items())}
