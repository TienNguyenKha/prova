"""Small statistics helpers for reporting repeated runs."""

import math
from collections.abc import Iterable
from statistics import mean, stdev


def mean_sem(values: Iterable[float]) -> tuple[float, float]:
    """Return (mean, standard error of the mean) of repeated measurements.

    SEM = sample standard deviation / sqrt(n). With fewer than 2 values there is no spread to estimate,
    so this raises instead of returning a misleading 0.
    """
    data = list(values)
    if len(data) < 2:
        raise ValueError(f"need at least 2 values to estimate the spread, got {len(data)}")
    return mean(data), stdev(data) / math.sqrt(len(data))
