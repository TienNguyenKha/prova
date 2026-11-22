"""Tests for prova.errors: values that end up in logs and results must stay stable."""

import pytest

from prova.errors import ErrorCode, ProvaError, ToolError


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NOT_FOUND", "not_found"),
        ("INVALID_ARGS", "invalid_args"),
        ("PERMISSION_DENIED", "permission_denied"),
        ("TIMEOUT", "timeout"),
        ("TRANSIENT", "transient"),
        ("SAFETY", "safety"),
        ("INTERNAL", "internal"),
    ],
)
def test_error_code_values_are_stable_strings(name: str, value: str) -> None:
    # These strings are written to trajectory logs and shown to the model: renaming one is a breaking change.
    assert ErrorCode[name].value == value
    assert ErrorCode(value) is ErrorCode[name]


def test_error_codes_format_as_their_value() -> None:
    assert f"{ErrorCode.TIMEOUT}" == "timeout"


def test_tool_error_keeps_code_and_message() -> None:
    error = ToolError(ErrorCode.SAFETY, "refused by a safety rule")
    assert error.code is ErrorCode.SAFETY
    assert str(error) == "refused by a safety rule"
    assert isinstance(error, ProvaError)
