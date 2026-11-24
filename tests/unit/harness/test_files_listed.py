"""Tests for prova.harness.verifiers.FilesListed: the agent may not stop until its report says what it did.

What it did is what the snapshots show. What it says it did is the report. The two must match, in both
directions: a file changed and not listed, and a file listed and not changed.
"""

from pathlib import Path
from typing import Any

import pytest

from prova.agent.loop import run_agent
from prova.config import Settings
from prova.harness.hooks import HookEvent, Hooks, run_hooks
from prova.harness.patterns import PathMatcher
from prova.harness.permissions import Permission
from prova.harness.report import FileChange, Report
from prova.harness.verifiers import FilesListed
from prova.tools.base import RunContext
from prova.tools.edit import EditFileTool
from prova.tools.report import SubmitResultTool
from tests.fakes import FakeLLM, make_tool_registry, text_message, tool_use_message


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return RunContext(workspace=tmp_path)


def change(ctx: RunContext, relative: str, content: str = "new\n") -> None:
    """Write a file the way a tool does: remember the original first."""
    path = ctx.workspace.resolve() / relative
    ctx.snapshots.ensure(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def report(*paths: str) -> Report:
    return Report(
        files_changed_with_reason=tuple(FileChange(path=p, reason="because") for p in paths),
        test_command="uv run pytest",
    )


def at_stop(ctx: RunContext) -> HookEvent:
    return HookEvent(kind="stop", ctx=ctx)


# --- No report, no stop ---------------------------------------------------------------------------


def test_the_agent_may_not_stop_before_it_hands_in_a_report(ctx: RunContext) -> None:
    decision = FilesListed()(at_stop(ctx))
    assert decision.action == "continue"
    assert "not handed in" in decision.message and "submit_result" in decision.message


# --- The report and the files must match ----------------------------------------------------------


def test_a_report_that_lists_exactly_the_files_that_changed_lets_the_run_stop(ctx: RunContext) -> None:
    change(ctx, "src/a.py")
    change(ctx, "src/new/b.py")
    ctx.submission.submit(report("src/a.py", "src/new/b.py"))
    assert FilesListed()(at_stop(ctx)).action == "allow"


def test_a_run_that_changed_nothing_and_says_so_may_stop(ctx: RunContext) -> None:
    ctx.submission.submit(report())
    assert FilesListed()(at_stop(ctx)).action == "allow"


def test_a_file_that_changed_and_was_not_listed_is_named(ctx: RunContext) -> None:
    change(ctx, "src/a.py")
    change(ctx, "src/forgotten.py")
    ctx.submission.submit(report("src/a.py"))
    decision = FilesListed()(at_stop(ctx))
    assert decision.action == "continue"
    assert "changed but not listed" in decision.message and "src/forgotten.py" in decision.message
    assert "listed but not changed" not in decision.message


def test_a_file_that_was_listed_and_did_not_change_is_named(ctx: RunContext) -> None:
    change(ctx, "src/a.py")
    ctx.submission.submit(report("src/a.py", "src/invented.py"))
    decision = FilesListed()(at_stop(ctx))
    assert decision.action == "continue"
    assert "listed but not changed" in decision.message and "src/invented.py" in decision.message
    assert "changed but not listed" not in decision.message


def test_both_kinds_of_mistake_are_reported_together(ctx: RunContext) -> None:
    change(ctx, "src/a.py")
    ctx.submission.submit(report("src/invented.py"))
    message = FilesListed()(at_stop(ctx)).message
    assert "src/a.py" in message and "src/invented.py" in message and "submit_result" in message


def test_a_file_edited_and_put_back_exactly_did_not_change_so_listing_it_is_wrong(ctx: RunContext) -> None:
    (ctx.workspace / "a.py").write_text("original\n", encoding="utf-8")
    change(ctx, "a.py", "something else\n")
    change(ctx, "a.py", "original\n")
    ctx.submission.submit(report("a.py"))
    assert "listed but not changed" in FilesListed()(at_stop(ctx)).message
    ctx.submission.submit(report())
    assert FilesListed()(at_stop(ctx)).action == "allow"


def test_a_report_handed_in_before_more_edits_is_caught_at_the_stop(ctx: RunContext) -> None:
    change(ctx, "a.py")
    ctx.submission.submit(report("a.py"))
    change(ctx, "b.py")  # a second edit, after the report
    assert "b.py" in FilesListed()(at_stop(ctx)).message


def test_a_long_difference_is_cut_and_the_cut_is_stated(ctx: RunContext) -> None:
    for n in range(15):
        change(ctx, f"src/file{n:02d}.py")
    ctx.submission.submit(report())
    message = FilesListed()(at_stop(ctx)).message
    assert "src/file00.py" in message and "src/file09.py" in message
    assert "src/file14.py" not in message and "and 5 more" in message


def test_through_the_pipeline_a_mismatch_holds_the_run_back(ctx: RunContext) -> None:
    change(ctx, "a.py")
    verdict = run_hooks((FilesListed(),), at_stop(ctx))
    assert verdict.blocked and verdict.outcomes[0].hook == "files_listed"


# --- In a real run --------------------------------------------------------------------------------


def edit_call(call_id: str = "e1") -> Any:
    return tool_use_message((call_id, "edit_file", {"path": "a.py", "old_str": "old", "new_str": "fixed"}))


def submit_call(call_id: str, *paths: str) -> Any:
    files = [{"path": p, "reason": "fixed the bug"} for p in paths]
    args = {"files_changed_with_reason": files, "test_command": "uv run pytest"}
    return tool_use_message((call_id, "submit_result", args))


def real_ctx(workspace: Path) -> RunContext:
    return RunContext(
        workspace=workspace,
        strict=True,
        permissions=frozenset({Permission.READ, Permission.WRITE}),
        write=PathMatcher(("**",)),
    )


def run(llm: FakeLLM, settings: Settings, workspace: Path) -> Any:
    return run_agent(
        "fix it",
        llm=llm,
        tools=make_tool_registry(EditFileTool(), SubmitResultTool()),
        ctx=real_ctx(workspace),
        settings=settings,
        system="",
        hooks=Hooks(stop=(FilesListed(),)),
    )


def test_the_agent_is_sent_back_until_its_report_is_right(settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")
    llm = FakeLLM(
        [
            edit_call(),
            text_message("done"),  # no report: sent back
            submit_call("s1", "b.py"),  # a report that is wrong
            text_message("done again"),  # sent back, with the names
            submit_call("s2", "a.py"),  # the right one
            text_message("now really done"),
        ]
    )

    result = run(llm, settings, tmp_path)

    assert (result.status, result.iterations) == ("done", 6)
    first, second = llm.messages.calls[2].messages[-1], llm.messages.calls[4].messages[-1]
    assert "not handed in" in first["content"]  # type: ignore[index, operator]
    assert "a.py" in second["content"] and "b.py" in second["content"]  # type: ignore[index, operator]


def test_an_agent_that_never_reports_ends_unverified_and_not_done(settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")
    llm = FakeLLM([edit_call(), text_message("done"), text_message("done"), text_message("done")])
    result = run(llm, settings, tmp_path)
    assert result.status == "unverified" and "not handed in" in result.detail
