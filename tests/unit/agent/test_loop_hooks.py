"""run_agent with hooks: rules around the loop. A hook can say no, add a note, or say "not yet"."""

import json
from pathlib import Path
from typing import Any

import pytest

from prova.agent.loop import AgentResult, run_agent
from prova.config import Settings
from prova.harness.hooks import HookDecision, HookEvent, Hooks
from prova.harness.permissions import Permission
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.tools.base import RunContext, Tool
from tests.fakes import (
    EchoTool,
    FakeLLM,
    WriterTool,
    make_tool_registry,
    text_message,
    tool_use_message,
)
from tests.unit.agent.test_loop_trajectory import DatetimeClock

KEY_LIKE = "sk-ant-api03-AbC123_xyz-9876-QwErTy"  # the shape of a key, not a real one


class Scripted:
    """A hook that answers from a script (the last answer repeats) and remembers every event it saw."""

    def __init__(self, name: str, *answers: HookDecision | Exception | object) -> None:
        self.name = name
        self._answers = list(answers) or [HookDecision.allow()]
        self.events: list[HookEvent] = []

    def __call__(self, ev: HookEvent) -> HookDecision:
        self.events.append(ev)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]


def go(
    llm: FakeLLM,
    settings: Settings,
    tmp_path: Path,
    *tools: Tool,
    hooks: Hooks,
    ctx: RunContext | None = None,
    max_iterations: int = 10,
) -> tuple[AgentResult, list[dict[str, Any]]]:
    log = tmp_path / "trajectory.jsonl"
    writer = TrajectoryWriter(log, run_id="run-1", clock=DatetimeClock(), redactor=Redactor())
    result = run_agent(
        "do the task",
        llm=llm,
        tools=make_tool_registry(*tools),
        ctx=ctx or RunContext(workspace=tmp_path, strict=True),
        settings=settings,
        system="You are careful.",
        max_iterations=max_iterations,
        trajectory=writer,
        hooks=hooks,
    )
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return result, records


def echo_call(call_id: str = "t1", text: str = "hi") -> Any:
    return tool_use_message((call_id, "echo", {"text": text}))


def answers_sent_to_the_model(result: AgentResult, index: int = 2) -> list[dict[str, Any]]:
    """The tool_result blocks of the user message at `index` (message 0 is the task)."""
    content: Any = result.messages[index]["content"]
    return list(content)


def hook_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["type"] == "hook_decision"]


# --- Before a tool: a hook can say no -----------------------------------------


def test_a_pre_hook_that_denies_stops_the_tool_and_tells_the_model_why(
    settings: Settings, tmp_path: Path
) -> None:
    tool = EchoTool()
    hook = Scripted("no_echo", HookDecision.deny("echo is switched off for this task"))
    llm = FakeLLM([echo_call(), text_message("ok")])

    result, records = go(llm, settings, tmp_path, tool, hooks=Hooks(pre=(hook,)))

    assert tool.calls == 0  # it never ran
    (block,) = answers_sent_to_the_model(result)
    assert block["is_error"] is True and "echo is switched off for this task" in block["content"]
    assert [r for r in records if r["type"] == "tool_result"][0]["error_code"] == "safety"


def test_a_pre_hook_that_allows_changes_nothing(settings: Settings, tmp_path: Path) -> None:
    tool = EchoTool()
    llm = FakeLLM([echo_call(text="hello"), text_message("ok")])
    result, _ = go(llm, settings, tmp_path, tool, hooks=Hooks(pre=(Scripted("fine"),)))
    assert tool.calls == 1
    (block,) = answers_sent_to_the_model(result)
    assert block["content"] == "hello" and "is_error" not in block


def test_a_pre_hook_sees_the_tool_name_the_arguments_and_the_context(
    settings: Settings, tmp_path: Path
) -> None:
    hook = Scripted("watch")
    ctx = RunContext(workspace=tmp_path, strict=True)
    go(
        FakeLLM([echo_call(text="x"), text_message("ok")]),
        settings,
        tmp_path,
        EchoTool(),
        hooks=Hooks(pre=(hook,)),
        ctx=ctx,
    )
    (event,) = hook.events
    assert (event.kind, event.tool, dict(event.args or {}), event.ctx) == (
        "pre_tool_use",
        "echo",
        {"text": "x"},
        ctx,
    )


def test_a_pre_hook_that_crashes_blocks_the_tool_and_the_model_never_sees_the_exception(
    settings: Settings, tmp_path: Path
) -> None:
    tool = EchoTool()
    hook = Scripted("audit", RuntimeError("secret-detail"))
    result, records = go(
        FakeLLM([echo_call(), text_message("ok")]), settings, tmp_path, tool, hooks=Hooks(pre=(hook,))
    )
    assert tool.calls == 0
    (block,) = answers_sent_to_the_model(result)
    assert "audit" in block["content"] and "secret-detail" not in block["content"]
    assert "secret-detail" in hook_events(records)[0]["error"]  # the log has it


def test_the_first_deny_wins_and_the_next_hook_is_not_asked(settings: Settings, tmp_path: Path) -> None:
    first, second = Scripted("first", HookDecision.deny("no")), Scripted("second")
    go(
        FakeLLM([echo_call(), text_message("ok")]),
        settings,
        tmp_path,
        EchoTool(),
        hooks=Hooks(pre=(first, second)),
    )
    assert len(first.events) == 1 and second.events == []


def test_a_hook_that_allows_cannot_lift_a_denial_by_the_permissions(
    settings: Settings, tmp_path: Path
) -> None:
    tool = WriterTool()
    hook = Scripted("generous")  # allows everything
    llm = FakeLLM([tool_use_message(("t1", "write_thing", {"text": "x"})), text_message("ok")])
    ctx = RunContext(workspace=tmp_path, strict=True, permissions=frozenset({Permission.READ}))

    result, _ = go(llm, settings, tmp_path, tool, hooks=Hooks(pre=(hook,)), ctx=ctx)

    assert tool.ran == 0
    (block,) = answers_sent_to_the_model(result)
    assert block["is_error"] is True and "permission_denied" in block["content"]
    assert len(hook.events) == 1  # the hook was still told: an audit hook must see every attempt


def test_a_blocked_call_is_still_answered_in_the_same_message_in_the_same_order(
    settings: Settings, tmp_path: Path
) -> None:
    tool = EchoTool()
    llm = FakeLLM(
        [tool_use_message(("t1", "echo", {"text": "a"}), ("t2", "echo", {"text": "b"})), text_message("ok")]
    )

    def deny_a(ev: HookEvent) -> HookDecision:
        return HookDecision.deny("not a") if (ev.args or {}).get("text") == "a" else HookDecision.allow()

    result, _ = go(llm, settings, tmp_path, tool, hooks=Hooks(pre=(deny_a,)))

    first, second = answers_sent_to_the_model(result)
    assert (first["tool_use_id"], first.get("is_error")) == ("t1", True)
    assert (second["tool_use_id"], second["content"]) == ("t2", "b")
    assert tool.calls == 1


# --- After a tool: a hook can add information ----------------------------------


def test_a_post_hook_note_is_shown_to_the_model_after_the_result(settings: Settings, tmp_path: Path) -> None:
    hook = Scripted("lint", HookDecision("allow", "2 style problems"))
    result, records = go(
        FakeLLM([echo_call(text="done"), text_message("ok")]),
        settings,
        tmp_path,
        EchoTool(),
        hooks=Hooks(post=(hook,)),
    )
    (block,) = answers_sent_to_the_model(result)
    assert block["content"] == "done\n\n[lint] 2 style problems"
    shown = [r for r in records if r["type"] == "tool_result"][0]["chars"]
    assert shown == len(block["content"])  # the log counts what the model really saw


def test_a_post_hook_sees_the_result_of_the_tool(settings: Settings, tmp_path: Path) -> None:
    hook = Scripted("look")
    go(
        FakeLLM([echo_call(text="done"), text_message("ok")]),
        settings,
        tmp_path,
        EchoTool(),
        hooks=Hooks(post=(hook,)),
    )
    (event,) = hook.events
    assert event.kind == "post_tool_use" and event.tool == "echo"
    assert event.result is not None and event.result.success and event.result.message == "done"


def test_a_post_hook_is_not_called_when_the_tool_never_ran(settings: Settings, tmp_path: Path) -> None:
    post = Scripted("lint")
    pre = Scripted("gate", HookDecision.deny("no"))
    go(
        FakeLLM([echo_call(), text_message("ok")]),
        settings,
        tmp_path,
        EchoTool(),
        hooks=Hooks(pre=(pre,), post=(post,)),
    )
    assert post.events == []


def test_a_post_hook_that_fails_leaves_a_note_that_its_check_did_not_run(
    settings: Settings, tmp_path: Path
) -> None:
    hook = Scripted("lint", RuntimeError("docker is gone"))
    result, _ = go(
        FakeLLM([echo_call(), text_message("ok")]), settings, tmp_path, EchoTool(), hooks=Hooks(post=(hook,))
    )
    (block,) = answers_sent_to_the_model(result)
    assert "[lint] this check did not run" in block["content"] and "docker is gone" not in block["content"]


# --- When the model wants to stop: a hook can say "not yet" --------------------


def test_a_stop_hook_that_says_not_yet_sends_its_reason_and_the_run_goes_on(
    settings: Settings, tmp_path: Path
) -> None:
    gate = Scripted(
        "gate", HookDecision.continue_run("3 tests fail: test_a, test_b, test_c"), HookDecision.allow()
    )
    llm = FakeLLM([text_message("I am done"), text_message("now really done")])

    result, _ = go(llm, settings, tmp_path, EchoTool(), hooks=Hooks(stop=(gate,)))

    assert (result.status, result.iterations, result.final_text) == ("done", 2, "now really done")
    sent_back: Any = llm.messages.calls[1].messages[-1]
    assert sent_back["role"] == "user" and "3 tests fail: test_a, test_b, test_c" in sent_back["content"]


def test_after_too_many_refusals_the_run_ends_unverified_not_done(settings: Settings, tmp_path: Path) -> None:
    gate = Scripted("gate", HookDecision.continue_run("tests are still red"))
    llm = FakeLLM([text_message("done 1"), text_message("done 2"), text_message("done 3")])

    result, records = go(llm, settings, tmp_path, EchoTool(), hooks=Hooks(stop=(gate,), max_stop_denials=2))

    assert result.status == "unverified" and result.iterations == 3
    assert result.final_text == "done 3"  # what the model said last is still kept
    assert "2" in result.detail and "tests are still red" in result.detail
    assert len(gate.events) == 3
    assert records[-1]["type"] == "outcome" and records[-1]["status"] == "unverified"


def test_with_no_refusals_allowed_the_first_no_ends_the_run_unverified(
    settings: Settings, tmp_path: Path
) -> None:
    gate = Scripted("gate", HookDecision.continue_run("red"))
    result, _ = go(
        FakeLLM([text_message("done")]), settings, tmp_path, hooks=Hooks(stop=(gate,), max_stop_denials=0)
    )
    assert result.status == "unverified" and result.iterations == 1


def test_a_stop_hook_that_crashes_keeps_the_run_from_ending_as_done(
    settings: Settings, tmp_path: Path
) -> None:
    gate = Scripted("gate", RuntimeError("sandbox down"))
    llm = FakeLLM([text_message("done"), text_message("done again")])
    result, _ = go(llm, settings, tmp_path, hooks=Hooks(stop=(gate,), max_stop_denials=1))
    assert result.status == "unverified"
    sent_back: Any = llm.messages.calls[1].messages[-1]
    assert "gate" in sent_back["content"] and "sandbox down" not in sent_back["content"]


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_the_stop_hook_is_only_asked_when_the_model_finishes_normally(
    settings: Settings, tmp_path: Path, stop_reason: str
) -> None:
    gate = Scripted("gate", HookDecision.continue_run("red"))
    llm = FakeLLM([text_message("cut", stop_reason=stop_reason)])
    result, _ = go(llm, settings, tmp_path, hooks=Hooks(stop=(gate,)))
    assert gate.events == [] and result.status in {"max_tokens", "refused"}


# --- The trajectory records every decision -------------------------------------


def test_every_hook_decision_is_in_the_trajectory_in_the_order_it_happened(
    settings: Settings, tmp_path: Path
) -> None:
    pre, post = Scripted("gate"), Scripted("lint", HookDecision("allow", "clean"))
    _, records = go(
        FakeLLM([echo_call(), text_message("ok")]),
        settings,
        tmp_path,
        EchoTool(),
        hooks=Hooks(pre=(pre,), post=(post,)),
    )
    sequence = [r["type"] for r in records if r["type"] in {"tool_call", "hook_decision", "tool_result"}]
    assert sequence == ["tool_call", "hook_decision", "hook_decision", "tool_result"]
    decisions = hook_events(records)
    assert [(e["hook_kind"], e["hook"], e["action"], e["tool"]) for e in decisions] == [
        ("pre_tool_use", "gate", "allow", "echo"),
        ("post_tool_use", "lint", "allow", "echo"),
    ]
    assert decisions[1]["message"] == "clean"


def test_a_denial_and_its_reason_are_in_the_trajectory(settings: Settings, tmp_path: Path) -> None:
    hook = Scripted("gate", HookDecision.deny("echo is off"))
    _, records = go(
        FakeLLM([echo_call(), text_message("ok")]), settings, tmp_path, EchoTool(), hooks=Hooks(pre=(hook,))
    )
    (event,) = hook_events(records)
    assert (event["action"], event["message"], event["error"]) == ("deny", "echo is off", None)


def test_a_stop_decision_is_in_the_trajectory_without_a_tool(settings: Settings, tmp_path: Path) -> None:
    gate = Scripted("gate", HookDecision.continue_run("red"), HookDecision.allow())
    _, records = go(
        FakeLLM([text_message("a"), text_message("b")]), settings, tmp_path, hooks=Hooks(stop=(gate,))
    )
    stops = [(e["hook_kind"], e["tool"], e["action"]) for e in hook_events(records)]
    assert stops == [("stop", None, "continue"), ("stop", None, "allow")]


def test_a_secret_in_a_hook_error_is_redacted_before_it_reaches_the_log(
    settings: Settings, tmp_path: Path
) -> None:
    hook = Scripted("audit", RuntimeError(f"cannot reach the service with {KEY_LIKE}"))
    _, records = go(
        FakeLLM([echo_call(), text_message("ok")]), settings, tmp_path, EchoTool(), hooks=Hooks(pre=(hook,))
    )
    assert KEY_LIKE not in json.dumps(records) and "[REDACTED]" in hook_events(records)[0]["error"]


def test_without_hooks_nothing_changes_and_no_hook_event_is_written(
    settings: Settings, tmp_path: Path
) -> None:
    result, records = go(
        FakeLLM([echo_call(), text_message("ok")]), settings, tmp_path, EchoTool(), hooks=Hooks()
    )
    assert result.status == "done" and hook_events(records) == []
