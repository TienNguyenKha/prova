"""Tests for prova.tools.report.SubmitResultTool: the agent hands in its report as data, not prose.

The tool only records. Whether the report is true is not its business: the stop hook compares it with
the files that really changed.
"""

from pathlib import Path
from typing import Any

import pytest

from prova.errors import ErrorCode
from prova.harness.permissions import Permission
from prova.harness.report import Report
from prova.tools.base import RunContext, ToolResult
from prova.tools.report import SubmitResultTool
from tests.fakes import make_tool_registry

GOOD: dict[str, Any] = {
    "files_changed_with_reason": [{"path": "src/a.py", "reason": "the regex missed two words"}],
    "test_command": "uv run pytest",
    "assumptions": ["the tests in tests/ are the whole suite"],
}


def submit(ctx: RunContext, report: dict[str, Any] | object) -> ToolResult:
    return make_tool_registry(SubmitResultTool()).dispatch("submit_result", report, ctx)


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return RunContext(workspace=tmp_path, strict=True)


# --- What the tool is -----------------------------------------------------------------------------------


def test_any_run_may_hand_in_a_report() -> None:
    assert SubmitResultTool().permission == Permission.READ


def test_the_fields_of_the_tool_are_the_fields_of_the_output_contract() -> None:
    schema = SubmitResultTool().args_model.model_json_schema()
    assert set(schema["properties"]) == {"files_changed_with_reason", "test_command", "assumptions"}
    assert set(schema["required"]) == {"files_changed_with_reason", "test_command"}  # assumptions may be none


# --- Recording ----------------------------------------------------------------------------------------------


def test_a_good_report_is_recorded_on_the_context(ctx: RunContext) -> None:
    result = submit(ctx, GOOD)
    assert result.success and "1 file" in result.message
    report = ctx.submission.report
    assert isinstance(report, Report)
    assert [(f.path, f.reason) for f in report.files_changed_with_reason] == [
        ("src/a.py", "the regex missed two words")
    ]
    assert report.test_command == "uv run pytest" and report.assumptions == (
        "the tests in tests/ are the whole suite",
    )


def test_a_run_that_changed_nothing_may_say_so(ctx: RunContext) -> None:
    result = submit(ctx, {**GOOD, "files_changed_with_reason": []})
    assert result.success and ctx.submission.report is not None
    assert ctx.submission.report.files_changed_with_reason == ()


def test_assumptions_are_optional(ctx: RunContext) -> None:
    only_required = {k: v for k, v in GOOD.items() if k != "assumptions"}
    assert submit(ctx, only_required).success
    assert ctx.submission.report is not None and ctx.submission.report.assumptions == ()


def test_a_second_report_replaces_the_first_and_the_model_is_told(ctx: RunContext) -> None:
    submit(ctx, GOOD)
    result = submit(ctx, {**GOOD, "test_command": "uv run pytest -q"})
    assert result.success and "replaces" in result.message
    assert ctx.submission.report is not None and ctx.submission.report.test_command == "uv run pytest -q"


def test_a_report_nobody_submitted_is_none(ctx: RunContext) -> None:
    assert ctx.submission.report is None


def test_every_run_context_has_its_own_submission(tmp_path: Path) -> None:
    first, second = RunContext(workspace=tmp_path), RunContext(workspace=tmp_path)
    submit(first, GOOD)
    assert second.submission.report is None


def test_a_recorded_report_cannot_be_changed(ctx: RunContext) -> None:
    submit(ctx, GOOD)
    assert ctx.submission.report is not None
    with pytest.raises(Exception, match="frozen"):
        ctx.submission.report.test_command = "rm -rf /"  # type: ignore[misc]


# --- A report that is wrong is refused at once, and nothing is recorded ------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "test_command": ""},
        {**GOOD, "files_changed_with_reason": [{"path": "a.py", "reason": ""}]},
        {**GOOD, "files_changed_with_reason": [{"path": "a.py", "reason": "x" * 301}]},
        {**GOOD, "files_changed_with_reason": [{"path": "", "reason": "why"}]},
        {**GOOD, "assumptions": [""]},
        {**GOOD, "assumptions": ["x"] * 21},
        {**GOOD, "surprise": "extra field"},
        {"test_command": "uv run pytest"},
    ],
)
def test_a_malformed_report_is_refused_and_nothing_is_recorded(ctx: RunContext, bad: dict[str, Any]) -> None:
    result = submit(ctx, bad)
    assert result.error_code == ErrorCode.INVALID_ARGS
    assert ctx.submission.report is None


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "a/../../outside.py", ".", "C:\\x.py"])
def test_a_path_that_is_not_inside_the_workspace_is_refused(ctx: RunContext, path: str) -> None:
    report = {**GOOD, "files_changed_with_reason": [{"path": path, "reason": "why"}]}
    result = submit(ctx, report)
    assert result.error_code == ErrorCode.INVALID_ARGS and "path" in result.message
    assert ctx.submission.report is None


def test_the_same_file_listed_twice_is_refused(ctx: RunContext) -> None:
    files = [{"path": "src/a.py", "reason": "one"}, {"path": "./src/a.py", "reason": "two"}]
    result = submit(ctx, {**GOOD, "files_changed_with_reason": files})
    assert result.error_code == ErrorCode.INVALID_ARGS and "twice" in result.message


def test_paths_are_written_down_in_one_normal_form(ctx: RunContext) -> None:
    files = [{"path": "./src//deep/../a.py", "reason": "why"}]
    submit(ctx, {**GOOD, "files_changed_with_reason": files})
    assert ctx.submission.report is not None
    assert ctx.submission.report.files_changed_with_reason[0].path == "src/a.py"


def test_the_arguments_must_be_an_object(ctx: RunContext) -> None:
    assert submit(ctx, "I changed src/a.py").error_code == ErrorCode.INVALID_ARGS
