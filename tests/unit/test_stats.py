"""Tests for prova.stats: mean and standard error of the mean."""

import math

import pytest

from prova.stats import mean_sem


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
