"""Tests for prova.sandbox.base.ExecResult."""

from dataclasses import FrozenInstanceError

import pytest

from prova.sandbox.base import ExecResult


def make(stdout: str = "", stderr: str = "") -> ExecResult:
    return ExecResult(
        exit_code=0, stdout=stdout, stderr=stderr, timed_out=False, duration_s=0.1, truncated=False
    )


def test_an_exec_result_cannot_be_changed() -> None:
    with pytest.raises(FrozenInstanceError):
        make().exit_code = 1  # type: ignore[misc]


def test_output_is_stdout_then_stderr() -> None:
    assert make("out\n", "err\n").output == "out\nerr\n"


def test_output_adds_a_line_break_between_the_two_when_stdout_has_none() -> None:
    assert make("out", "err").output == "out\nerr"


def test_output_is_just_the_one_that_has_text() -> None:
    assert make("out").output == "out" and make(stderr="err").output == "err" and make().output == ""
