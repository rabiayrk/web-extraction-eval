from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def bootstrap_ci(values: Sequence[float], n_boot: int = 2000, alpha: float = 0.05,
                 seed: int = 0) -> tuple[float, float, float]:
    """Mean and percentile bootstrap interval over independent units (site instances)."""
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n_boot, len(arr)))
    means = arr[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(arr.mean()), float(lo), float(hi)


def paired_bootstrap(a: Sequence[float], b: Sequence[float], n_boot: int = 2000,
                     seed: int = 0) -> tuple[float, float, float, float]:
    """Mean difference a - b over paired units, its 95% interval and a two-sided p-value."""
    diff = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    if len(diff) == 0:
        return (float("nan"),) * 4
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diff), size=(n_boot, len(diff)))
    means = diff[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    # Share of resamples on the other side of zero, doubled.
    other_side = means <= 0 if diff.mean() >= 0 else means >= 0
    p = 2 * float(other_side.mean())
    return float(diff.mean()), float(lo), float(hi), min(1.0, p)


def sign_flip_test(diffs: Sequence[float]) -> float:
    """Exact two-sided paired permutation test over all sign patterns."""
    arr = np.asarray(diffs, dtype=float)
    n = len(arr)
    if n == 0 or n > 20:
        raise ValueError("needs 1 to 20 paired units")
    observed = abs(arr.mean())
    signs = np.array(np.meshgrid(*[[-1.0, 1.0]] * n)).reshape(n, -1).T
    means = np.abs((signs * arr).mean(axis=1))
    return float((means >= observed - 1e-12).mean())


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm adjusted p-values, in the input order."""
    order = np.argsort(pvalues)
    m = len(pvalues)
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvalues[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted
