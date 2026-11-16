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
