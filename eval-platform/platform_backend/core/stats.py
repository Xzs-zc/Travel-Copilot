from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def pass_at_k(n: int, c: int, k: int) -> float:
    if n <= 0 or c < 0 or c > n or k <= 0:
        raise ValueError("invalid_pass_at_k_arguments")
    k = min(k, n)
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    if n <= 0 or c < 0 or c > n or k <= 0:
        raise ValueError("invalid_pass_hat_k_arguments")
    k = min(k, n)
    if c < k:
        return 0.0
    return math.comb(c, k) / math.comb(n, k)


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float],
    *,
    iters: int = 2000,
    seed: int = 42,
) -> tuple[float, float] | None:
    if not values or len(set(values)) == 1:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(iters):
        sample = [values[rng.randrange(len(values))] for _ in values]
        estimates.append(stat(sample))
    estimates.sort()
    lower = estimates[int(0.025 * (len(estimates) - 1))]
    upper = estimates[int(0.975 * (len(estimates) - 1))]
    return lower, upper


def paired_bootstrap_ci(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    iters: int = 2000,
    seed: int = 42,
) -> tuple[float, float] | None:
    if len(baseline) != len(candidate):
        raise ValueError("paired_vectors_must_match")
    differences = [
        float(after) - float(before)
        for before, after in zip(baseline, candidate, strict=True)
    ]
    return bootstrap_ci(
        differences,
        lambda sample: sum(sample) / len(sample),
        iters=iters,
        seed=seed,
    )


def mcnemar(b: int, c: int) -> float:
    if b < 0 or c < 0:
        raise ValueError("mcnemar_counts_must_be_non_negative")
    discordant = b + c
    if discordant == 0:
        return 1.0
    if discordant <= 200:
        tail = sum(
            math.comb(discordant, index)
            for index in range(min(b, c) + 1)
        ) / (2**discordant)
        return min(1.0, 2 * tail)
    statistic = (abs(b - c) - 1) ** 2 / discordant
    return math.erfc(math.sqrt(statistic / 2))
