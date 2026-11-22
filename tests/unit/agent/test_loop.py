"""Tests for prova.agent.loop.run_agent: the ReAct loop, driven by a fake model with canned replies."""

from pathlib import Path
from typing import Any

import pytest

from prova.agent.loop import AgentResult, run_agent
from prova.config import Settings
from prova.errors import LLMError
from prova.tools.base import Permission, RunContext, Tool
from tests.fakes import (
    BuggyTool,
    EchoTool,
    FakeLLM,
    WriterTool,
    make_tool_registry,
    text_message,
    tool_use_message,
)

SYSTEM = "You are a careful assistant."
TASK = "find the answer"


class StepClock:
    """A clock that moves 0.5 s every time it is read, so latencies are exact in tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.5
        return self.now


def start(
    llm: FakeLLM,
    settings: Settings,
    workspace: Path,
    *tools: Tool,
    strict: bool = True,
    max_iterations: int = 30,
) -> AgentResult:
    ctx = RunContext(workspace=workspace, strict=strict)
    return run_agent(
        TASK,
        llm=llm,
        tools=make_tool_registry(*tools),
        ctx=ctx,
        settings=settings,
        system=SYSTEM,
        max_iterations=max_iterations,
        clock=StepClock(),
    )


def results_in(message: Any) -> list[dict[str, Any]]:
    return [block for block in message["content"] if block["type"] == "tool_result"]


# --- A plain answer ----------------------------------------------------------


def test_an_answer_without_tools_ends_the_run(settings: Settings, tmp_path: Path) -> None:
    result = start(FakeLLM([text_message("42")]), settings, tmp_path, EchoTool())

    assert result.status == "done"
    assert result.final_text == "42"
    assert result.iterations == 1
    assert [m["role"] for m in result.messages] == ["user", "assistant"]
    assert result.messages[0] == {"role": "user", "content": TASK}


def test_the_request_carries_the_model_the_system_prompt_the_task_and_the_offered_tools(
    settings: Settings, tmp_path: Path
) -> None:
    llm = FakeLLM([text_message("42")])
    start(llm, settings, tmp_path, EchoTool(), WriterTool())

    (call,) = llm.messages.calls
    assert call.model == settings.model
    assert call.max_tokens == settings.max_tokens
    assert call.system == SYSTEM
    assert call.messages == [{"role": "user", "content": TASK}]
    assert [t["name"] for t in call.tools] == ["echo"]  # a read-only run is not offered the write tool


def test_every_model_call_is_recorded_in_the_usage_log(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([tool_use_message(("t1", "echo", {"text": "x"})), text_message("done")])
    result = start(llm, settings, tmp_path, EchoTool())

    assert [r.turn for r in result.usage.records] == [1, 2]
    assert result.usage.total_input_tokens == 2  # 1 token per canned reply
    assert [r.latency_s for r in result.usage.records] == [0.5, 0.5]  # clock read once before, once after


# --- Using tools --------------------------------------------------------------


def test_a_tool_call_is_run_and_its_result_goes_back_to_the_model(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([tool_use_message(("toolu_1", "echo", {"text": "hi"})), text_message("all done")])
    result = start(llm, settings, tmp_path, EchoTool())

    assert (result.status, result.final_text, result.iterations) == ("done", "all done", 2)
    second_request = llm.messages.calls[1].messages
    assert [m["role"] for m in second_request] == ["user", "assistant", "user"]
    (block,) = results_in(second_request[2])
    assert block["tool_use_id"] == "toolu_1"
    assert block["content"] == "hi"
    assert any(getattr(b, "type", None) == "tool_use" for b in second_request[1]["content"])  # kept as is
    assert [m["role"] for m in result.messages] == ["user", "assistant", "user", "assistant"]


def test_several_tool_calls_in_one_turn_are_answered_in_one_message_in_order(
    settings: Settings, tmp_path: Path
) -> None:
    calls = (("t1", "echo", {"text": "a"}), ("t2", "echo", {"text": "b"}))
    llm = FakeLLM([tool_use_message(*calls), text_message("done")])
    start(llm, settings, tmp_path, EchoTool())

    second_request = llm.messages.calls[1].messages
    assert len(second_request) == 3  # ONE user message for both results, not two
    answered = [(b["tool_use_id"], b["content"]) for b in results_in(second_request[2])]
    assert answered == [("t1", "a"), ("t2", "b")]


def test_a_failing_tool_call_comes_back_as_an_error_and_the_run_goes_on(
    settings: Settings, tmp_path: Path
) -> None:
    llm = FakeLLM([tool_use_message(("t1", "no_such_tool", {})), text_message("I will answer directly")])
    result = start(llm, settings, tmp_path, EchoTool())

    assert result.status == "done"
    (block,) = results_in(llm.messages.calls[1].messages[2])
    assert block["is_error"] is True
    assert block["content"].startswith("not_found:")


def test_a_tool_the_run_may_not_use_is_denied_even_if_the_model_asks_for_it(
    settings: Settings, tmp_path: Path
) -> None:
    writer = WriterTool()
    llm = FakeLLM([tool_use_message(("t1", "write_thing", {"text": "x"})), text_message("ok")])
    start(llm, settings, tmp_path, EchoTool(), writer)

    (block,) = results_in(llm.messages.calls[1].messages[2])
    assert block["is_error"] is True
    assert block["content"].startswith("permission_denied:")
    assert writer.ran == 0


def test_a_bug_in_a_tool_is_reported_to_the_model_and_the_run_goes_on_when_not_strict(
    settings: Settings, tmp_path: Path
) -> None:
    llm = FakeLLM([tool_use_message(("t1", "buggy", {"text": "x"})), text_message("recovered")])
    result = start(llm, settings, tmp_path, BuggyTool(), strict=False)

    assert result.status == "done"
    (block,) = results_in(llm.messages.calls[1].messages[2])
    assert block["content"].startswith("internal:")
    assert "secret-detail" not in block["content"]


def test_in_strict_mode_a_bug_in_a_tool_stops_the_run(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([tool_use_message(("t1", "buggy", {"text": "x"}))])
    with pytest.raises(RuntimeError, match="secret-detail"):
        start(llm, settings, tmp_path, BuggyTool(), strict=True)


# --- The loop always ends ----------------------------------------------------


def test_a_model_that_never_stops_asking_for_tools_is_cut_off_at_max_iterations(
    settings: Settings, tmp_path: Path
) -> None:
    replies = [tool_use_message((f"t{i}", "echo", {"text": "x"})) for i in range(3)]
    llm = FakeLLM(replies)
    result = start(llm, settings, tmp_path, EchoTool(), max_iterations=3)

    assert result.status == "max_iterations"
    assert result.iterations == 3
    assert len(llm.messages.calls) == 3  # exactly three calls were made, not four
    assert result.final_text == ""


def test_a_failed_model_call_ends_the_run_with_status_error_instead_of_raising(
    settings: Settings, tmp_path: Path
) -> None:
    llm = FakeLLM([tool_use_message(("t1", "echo", {"text": "x"})), LLMError("overloaded", status_code=503)])
    result = start(llm, settings, tmp_path, EchoTool())

    assert result.status == "error"
    assert "overloaded" in result.detail and "503" in result.detail
    assert result.iterations == 2
    assert len(result.usage.records) == 1  # only the call that succeeded is recorded
    assert [m["role"] for m in result.messages] == ["user", "assistant", "user"]  # what happened is kept


def test_the_permissions_of_the_run_decide_which_tools_are_offered(
    settings: Settings, tmp_path: Path
) -> None:
    llm = FakeLLM([text_message("done")])
    ctx = RunContext(workspace=tmp_path, permissions=frozenset({Permission.READ, Permission.WRITE}))
    run_agent(
        TASK,
        llm=llm,
        tools=make_tool_registry(EchoTool(), WriterTool()),
        ctx=ctx,
        settings=settings,
        system=SYSTEM,
    )
    assert [t["name"] for t in llm.messages.calls[0].tools] == ["echo", "write_thing"]


# --- Replies that did not end normally: stop, never guess ---------------------


def test_a_reply_cut_at_max_tokens_never_runs_its_tool_calls(settings: Settings, tmp_path: Path) -> None:
    echo = EchoTool()
    cut = tool_use_message(("t1", "echo", {"text": "x"}), text="Let me", stop_reason="max_tokens")
    llm = FakeLLM([cut])
    result = start(llm, settings, tmp_path, echo)

    assert result.status == "max_tokens"
    assert result.final_text == "Let me"  # what the model managed to say
    assert "max_tokens" in result.detail
    assert echo.calls == 0  # the arguments of a call that was cut off may be incomplete
    assert len(llm.messages.calls) == 1  # and the model is not asked again
    assert result.messages[-1]["role"] == "assistant"


def test_a_text_reply_cut_at_max_tokens_is_also_reported(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([text_message("The answer is probably", stop_reason="max_tokens")])
    result = start(llm, settings, tmp_path, EchoTool())
    assert (result.status, result.final_text) == ("max_tokens", "The answer is probably")


def test_a_cut_reply_late_in_the_run_keeps_the_count_and_history(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM(
        [tool_use_message(("t1", "echo", {"text": "x"})), text_message("so far", stop_reason="max_tokens")]
    )
    result = start(llm, settings, tmp_path, EchoTool())
    assert (result.status, result.iterations) == ("max_tokens", 2)
    assert [m["role"] for m in result.messages] == ["user", "assistant", "user", "assistant"]


def test_a_refusal_ends_the_run_and_says_why(settings: Settings, tmp_path: Path) -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "looks like exploit work"}
    llm = FakeLLM([text_message("I can't help with that.", stop_reason="refusal", stop_details=details)])
    result = start(llm, settings, tmp_path, EchoTool())

    assert result.status == "refused"
    assert result.final_text == "I can't help with that."
    assert "cyber" in result.detail and "looks like exploit work" in result.detail
    assert len(llm.messages.calls) == 1  # asking again would not change the answer, only the bill


def test_a_refusal_without_details_still_ends_the_run(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([text_message("no", stop_reason="refusal")])
    result = start(llm, settings, tmp_path, EchoTool())
    assert (result.status, result.detail) == ("refused", "the model refused")


@pytest.mark.parametrize("stop_reason", ["pause_turn", "stop_sequence", "model_context_window_exceeded"])
def test_a_stop_reason_we_cannot_handle_ends_the_run_without_running_tools(
    settings: Settings, tmp_path: Path, stop_reason: str
) -> None:
    echo = EchoTool()
    llm = FakeLLM([tool_use_message(("t1", "echo", {"text": "x"}), stop_reason=stop_reason)])
    result = start(llm, settings, tmp_path, echo)

    assert result.status == "error"
    assert stop_reason in result.detail  # the reason is named, so it can be fixed
    assert echo.calls == 0
    assert len(llm.messages.calls) == 1  # no blind retry


@pytest.mark.parametrize("stop_reason", ["a_reason_added_next_year", None])
def test_an_unknown_or_missing_stop_reason_ends_the_run(
    settings: Settings, tmp_path: Path, stop_reason: str | None
) -> None:
    reply = text_message("hello")
    reply.stop_reason = stop_reason  # pyright: ignore[reportAttributeAccessIssue]
    llm = FakeLLM([reply])
    result = start(llm, settings, tmp_path, EchoTool())

    assert result.status == "error"
    assert repr(stop_reason) in result.detail
    assert len(llm.messages.calls) == 1
