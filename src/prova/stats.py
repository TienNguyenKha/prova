"""Small statistics helpers for reporting repeated runs."""

import math
from collections.abc import Iterable
from statistics import mean, stdev

# Two-sided 95% critical values of Student's t, by degrees of freedom. With few runs the interval is much
# wider than the 1.96 of a normal curve, and 1.96 would make a result look surer than it is.
_T_95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
)  # fmt: skip


def mean_sem(values: Iterable[float]) -> tuple[float, float]:
    """Return (mean, standard error of the mean) of repeated measurements.

    SEM = sample standard deviation / sqrt(n). With fewer than 2 values there is no spread to estimate,
    so this raises instead of returning a misleading 0.
    """
    data = list(values)
    if len(data) < 2:
        raise ValueError(f"need at least 2 values to estimate the spread, got {len(data)}")
    return mean(data), stdev(data) / math.sqrt(len(data))


def t_critical_95(df: int) -> float:
    """The t value for a 95% interval with `df` degrees of freedom (runs minus one), for 1 to 20."""
    if not 1 <= df <= len(_T_95):
        raise ValueError(f"no t value for {df} degrees of freedom: the table covers 1 to {len(_T_95)}")
    return _T_95[df - 1]


def mean_ci95(values: Iterable[float]) -> tuple[float, float, float]:
    """Return (mean, low, high): the mean and its 95% confidence interval, from at least 2 values."""
    data = list(values)
    average, sem = mean_sem(data)
    half = t_critical_95(len(data) - 1) * sem
    return average, average - half, average + half


def percentile(values: Iterable[float], p: float) -> float:
    """The `p`-th percentile by nearest rank: the smallest value that at least `p` percent of the values do
    not exceed. It is always a value that was measured, never one made up between two of them."""
    if not 0 < p <= 100:  # noqa: PLR2004 - a percentage
        raise ValueError(f"p must be between 0 (not included) and 100, got {p}")
    data = sorted(values)
    if not data:
        raise ValueError("there are no values to take a percentile of")
    return data[math.ceil(p * len(data) / 100) - 1]
