"""Tests for prova.harness.hooks: rules that sit around the loop. A hook can only make things stricter."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from prova.harness.hooks import (
    HookDecision,
    HookEvent,
    Hooks,
    hook_name,
    run_hooks,
)
from prova.tools.base import RunContext, ToolResult


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return RunContext(workspace=tmp_path)


def pre(ctx: RunContext, tool: str = "read_file", args: dict[str, Any] | None = None) -> HookEvent:
    return HookEvent(kind="pre_tool_use", ctx=ctx, tool=tool, args=args or {"path": "a.py"})


def post(ctx: RunContext) -> HookEvent:
    result = ToolResult.ok("edited a.py")
    return HookEvent(kind="post_tool_use", ctx=ctx, tool="edit_file", args={"path": "a.py"}, result=result)


def stop(ctx: RunContext) -> HookEvent:
    return HookEvent(kind="stop", ctx=ctx)


class Recorder:
    """A hook that answers as told and remembers that it was called."""

    def __init__(self, name: str, decision: object) -> None:
        self.name = name
        self._decision = decision
        self.calls: list[HookEvent] = []

    def __call__(self, ev: HookEvent) -> HookDecision:
        self.calls.append(ev)
        if isinstance(self._decision, Exception):
            raise self._decision
        return self._decision  # type: ignore[return-value]  # tests also send wrong things on purpose


# --- The decision -------------------------------------------------------------


def test_the_three_decisions() -> None:
    assert HookDecision.allow() == HookDecision("allow", "")
    assert HookDecision.deny("no") == HookDecision("deny", "no")
    assert HookDecision.continue_run("tests are red") == HookDecision("continue", "tests are red")


@pytest.mark.parametrize("make", [HookDecision.deny, HookDecision.continue_run])
def test_a_deny_or_continue_must_say_why_because_the_model_reads_it(make: Any) -> None:
    with pytest.raises(ValueError, match="message"):
        make("")


def test_a_decision_cannot_be_changed(ctx: RunContext) -> None:
    with pytest.raises(FrozenInstanceError):
        HookDecision.allow().action = "deny"  # type: ignore[misc]


def test_hooks_are_a_frozen_set_with_a_limit_on_how_often_stop_may_say_no() -> None:
    hooks = Hooks()
    assert hooks.pre == () and hooks.post == () and hooks.stop == ()
    assert hooks.max_stop_denials == 2
    with pytest.raises(FrozenInstanceError):
        hooks.max_stop_denials = 5  # type: ignore[misc]
    with pytest.raises(ValueError, match="max_stop_denials"):
        Hooks(max_stop_denials=-1)


def test_a_hook_is_named_by_its_name_attribute_its_function_name_or_its_class() -> None:
    def my_check(ev: HookEvent) -> HookDecision:
        return HookDecision.allow()

    class Audit:
        def __call__(self, ev: HookEvent) -> HookDecision:
            return HookDecision.allow()

    assert hook_name(Recorder("lint", HookDecision.allow())) == "lint"
    assert hook_name(my_check) == "my_check"
    assert hook_name(Audit()) == "Audit"


# --- Before a tool runs: a hook can only say no --------------------------------


def test_with_no_hooks_nothing_is_blocked(ctx: RunContext) -> None:
    verdict = run_hooks((), pre(ctx))
    assert not verdict.blocked and verdict.outcomes == () and verdict.notes == ()


def test_when_every_hook_allows_the_tool_runs_and_each_decision_is_recorded_in_order(ctx: RunContext) -> None:
    first, second = Recorder("first", HookDecision.allow()), Recorder("second", HookDecision.allow())
    verdict = run_hooks((first, second), pre(ctx))
    assert not verdict.blocked
    assert [o.hook for o in verdict.outcomes] == ["first", "second"]
    assert [o.action for o in verdict.outcomes] == ["allow", "allow"]


def test_the_first_deny_wins_and_the_hooks_after_it_are_not_called(ctx: RunContext) -> None:
    allow = Recorder("allow", HookDecision.allow())
    deny = Recorder("deny", HookDecision.deny("path is not for the model"))
    never = Recorder("never", HookDecision.allow())
    verdict = run_hooks((allow, deny, never), pre(ctx))
    assert verdict.blocked and verdict.message == "path is not for the model"
    assert [o.hook for o in verdict.outcomes] == ["allow", "deny"]
    assert never.calls == []


def test_a_hook_that_raises_blocks_the_tool_and_the_model_is_not_shown_the_exception(ctx: RunContext) -> None:
    broken = Recorder("audit", RuntimeError("database password is hunter2"))
    verdict = run_hooks((broken,), pre(ctx))
    assert verdict.blocked
    assert "audit" in verdict.message and "hunter2" not in verdict.message
    assert verdict.outcomes[0].action == "deny"
    logged = verdict.outcomes[0].error  # only the log has the exception text
    assert logged is not None and "hunter2" in logged


def test_continue_means_nothing_before_a_tool_so_it_blocks(ctx: RunContext) -> None:
    verdict = run_hooks((Recorder("odd", HookDecision.continue_run("keep going")),), pre(ctx))
    assert verdict.blocked and "odd" in verdict.message


@pytest.mark.parametrize("answer", [None, "allow", True, {"action": "allow"}])
def test_an_answer_that_is_not_a_hook_decision_blocks(ctx: RunContext, answer: object) -> None:
    verdict = run_hooks((Recorder("sloppy", answer),), pre(ctx))
    assert verdict.blocked and "sloppy" in verdict.message


def test_the_hook_is_given_the_event_with_the_tool_and_its_arguments(ctx: RunContext) -> None:
    seen = Recorder("seen", HookDecision.allow())
    event = pre(ctx, "edit_file", {"path": "x.py"})
    run_hooks((seen,), event)
    assert seen.calls == [event] and event.tool == "edit_file" and event.args == {"path": "x.py"}


# --- After a tool ran: a hook can only add information -------------------------


def test_a_message_from_an_allowing_hook_becomes_a_note_for_the_model(ctx: RunContext) -> None:
    verdict = run_hooks((Recorder("lint", HookDecision("allow", "ruff: 2 problems")),), post(ctx))
    assert not verdict.blocked
    assert verdict.notes == ("[lint] ruff: 2 problems",)


def test_an_allowing_hook_with_nothing_to_say_adds_no_note(ctx: RunContext) -> None:
    assert run_hooks((Recorder("lint", HookDecision.allow()),), post(ctx)).notes == ()


def test_a_deny_after_the_tool_ran_cannot_undo_it_and_is_reported_as_misuse(ctx: RunContext) -> None:
    verdict = run_hooks((Recorder("late", HookDecision.deny("too late")),), post(ctx))
    assert not verdict.blocked
    assert len(verdict.notes) == 1
    assert "late" in verdict.notes[0] and "only add information" in verdict.notes[0]


def test_a_hook_that_fails_after_the_tool_leaves_a_note_that_its_check_did_not_run(ctx: RunContext) -> None:
    verdict = run_hooks((Recorder("lint", RuntimeError("docker is gone")),), post(ctx))
    assert not verdict.blocked
    assert verdict.notes == ("[lint] this check did not run: the hook failed",)
    assert verdict.outcomes[0].error is not None and "docker is gone" in verdict.outcomes[0].error


def test_every_hook_after_a_tool_runs_even_when_an_earlier_one_failed(ctx: RunContext) -> None:
    broken = Recorder("broken", RuntimeError("x"))
    fine = Recorder("fine", HookDecision("allow", "all good"))
    verdict = run_hooks((broken, fine), post(ctx))
    assert len(fine.calls) == 1
    assert verdict.notes == ("[broken] this check did not run: the hook failed", "[fine] all good")


# --- When the model wants to stop: a hook can keep it going -------------------


def test_when_every_stop_hook_allows_the_run_may_end(ctx: RunContext) -> None:
    assert not run_hooks((Recorder("gate", HookDecision.allow()),), stop(ctx)).blocked


def test_a_stop_hook_can_keep_the_run_going_and_its_message_goes_to_the_model(ctx: RunContext) -> None:
    gate = Recorder("gate", HookDecision.continue_run("3 tests fail: test_a, test_b, test_c"))
    later = Recorder("later", HookDecision.allow())
    verdict = run_hooks((gate, later), stop(ctx))
    assert verdict.blocked and verdict.message == "3 tests fail: test_a, test_b, test_c"
    assert later.calls == []


def test_a_stop_hook_that_fails_keeps_the_run_going_because_nothing_was_verified(ctx: RunContext) -> None:
    verdict = run_hooks((Recorder("gate", RuntimeError("sandbox down")),), stop(ctx))
    assert verdict.blocked and "gate" in verdict.message and "sandbox down" not in verdict.message
    assert verdict.outcomes[0].action == "continue"


def test_a_deny_at_stop_is_taken_as_keep_going(ctx: RunContext) -> None:
    verdict = run_hooks((Recorder("gate", HookDecision.deny("not yet")),), stop(ctx))
    assert verdict.blocked and verdict.message == "not yet"
