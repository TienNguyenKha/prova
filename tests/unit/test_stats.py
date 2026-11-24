"""Tests for prova.stats: mean and standard error of the mean."""

import math

import pytest

from prova.stats import mean_ci95, mean_sem, percentile, t_critical_95


def test_mean_and_sem_of_three_values() -> None:
    mean, sem = mean_sem([1.0, 2.0, 3.0])
    assert mean == pytest.approx(2.0)
    assert sem == pytest.approx(1.0 / math.sqrt(3))  # sample stdev 1.0, n = 3


def test_identical_values_have_zero_sem() -> None:
    assert mean_sem([0.5, 0.5, 0.5]) == (0.5, 0.0)


def test_accepts_any_iterable_of_numbers() -> None:
    mean, _ = mean_sem(x / 10 for x in (1, 2, 3))
    assert mean == pytest.approx(0.2)


@pytest.mark.parametrize("values", [[], [1.0]])
def test_fewer_than_two_values_is_an_error(values: list[float]) -> None:
    # One run has no spread to estimate; the project rule is at least 3 runs, so refuse rather than report 0.
    with pytest.raises(ValueError, match="at least 2"):
        mean_sem(values)


# --- the 95% interval ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("df", "expected"), [(1, 12.706), (2, 4.303), (9, 2.262), (20, 2.086)])
def test_the_t_value_for_few_runs_is_far_above_1_96(df: int, expected: float) -> None:
    # With 3 seeds there are 2 degrees of freedom: the interval is more than twice as wide as 1.96 would say.
    assert t_critical_95(df) == pytest.approx(expected)


@pytest.mark.parametrize("df", [0, -1, 21])
def test_a_t_value_outside_the_table_is_an_error_and_not_a_guess(df: int) -> None:
    with pytest.raises(ValueError, match="degrees of freedom"):
        t_critical_95(df)


def test_the_interval_of_three_values_is_the_mean_plus_or_minus_t_times_sem() -> None:
    mean, low, high = mean_ci95([1.0, 2.0, 3.0])
    half = 4.303 / math.sqrt(3)  # t for 2 degrees of freedom, sem = 1 / sqrt(3)
    assert (mean, low, high) == (pytest.approx(2.0), pytest.approx(2.0 - half), pytest.approx(2.0 + half))


def test_the_interval_of_identical_values_has_no_width() -> None:
    assert mean_ci95([0.5, 0.5, 0.5]) == (0.5, 0.5, 0.5)


def test_one_value_has_no_interval() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        mean_ci95([1.0])


# --- percentile ---------------------------------------------------------------------------------------


def test_the_95th_percentile_is_the_value_below_which_95_percent_fall_by_nearest_rank() -> None:
    assert percentile([float(i) for i in range(1, 101)], 95) == 95.0
    assert percentile([30.0, 10.0, 20.0], 95) == 30.0  # not sorted on input; 3 values: rank 3
    assert percentile([5.0], 95) == 5.0
    assert percentile([1.0, 2.0, 3.0], 100) == 3.0  # 100 is allowed: the largest value


@pytest.mark.parametrize("p", [0, -5, 100.5])
def test_a_percentile_outside_0_to_100_is_an_error(p: float) -> None:
    with pytest.raises(ValueError, match="between 0"):
        percentile([1.0], p)


def test_the_percentile_of_nothing_is_an_error_and_not_zero() -> None:
    with pytest.raises(ValueError, match="no values"):
        percentile([], 95)
