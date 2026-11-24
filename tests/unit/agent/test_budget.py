"""A run has a budget: tool calls, seconds and money. Over it, the run stops with status "budget".

Tool calls are checked before they run (a reply that would go over is not run at all). Seconds and money are
checked between turns, so one turn can go over by the cost of that turn: the limit is a fence, not a scale.
"""

from pathlib import Path
from typing import Any

import pytest

from prova.agent.budget import Budget, Pricing
from prova.agent.loop import AgentResult, run_agent
from prova.config import Settings
from prova.llm.usage import UsageLog, UsageRecord
from prova.tools.base import RunContext, ToolResult
from tests.fakes import EchoArgs, EchoTool, FakeLLM, make_tool_registry, text_message, tool_use_message

DOLLAR_PER_TOKEN = Pricing(input_per_mtok=1_000_000, output_per_mtok=1_000_000)


def log_of(*records: tuple[int, int]) -> UsageLog:
    log = UsageLog()
    for turn, (tokens_in, tokens_out) in enumerate(records, start=1):
        log.add(UsageRecord(turn=turn, input_tokens=tokens_in, output_tokens=tokens_out))
    return log


# --- Pricing ---------------------------------------------------------------------------------------


def test_the_cost_is_input_and_output_tokens_at_their_price_per_million() -> None:
    price = Pricing(input_per_mtok=3.0, output_per_mtok=15.0)
    assert price.cost(log_of((1_000_000, 0))) == pytest.approx(3.0)
    assert price.cost(log_of((0, 1_000_000))) == pytest.approx(15.0)
    # two turns: 1,000,000 tokens in (3.0) and 200,000 tokens out (3.0)
    assert price.cost(log_of((500_000, 100_000), (500_000, 100_000))) == pytest.approx(6.0)


def test_tokens_that_have_no_price_stop_the_count_instead_of_being_guessed() -> None:
    log = UsageLog()
    log.add(UsageRecord(turn=1, input_tokens=1, output_tokens=1, cache_read_input_tokens=5))
    with pytest.raises(ValueError, match="cache"):
        DOLLAR_PER_TOKEN.cost(log)


@pytest.mark.parametrize("bad", [0, -1.0])
def test_a_price_must_be_positive(bad: float) -> None:
    with pytest.raises(ValueError, match="price"):
        Pricing(input_per_mtok=bad, output_per_mtok=1.0)
    with pytest.raises(ValueError, match="price"):
        Pricing(input_per_mtok=1.0, output_per_mtok=bad)


# --- Budget ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["max_tool_calls", "max_seconds", "max_cost_usd"])
@pytest.mark.parametrize("bad", [0, -1])
def test_a_limit_must_be_positive_or_absent(field: str, bad: int) -> None:
    with pytest.raises(ValueError, match=field):
        Budget(**{field: bad}, pricing=DOLLAR_PER_TOKEN)  # type: ignore[arg-type]


def test_a_money_limit_needs_a_price_so_that_it_cannot_be_silently_ignored() -> None:
    with pytest.raises(ValueError, match="pricing"):
        Budget(max_cost_usd=1.0)


def test_a_budget_with_no_limit_is_never_over() -> None:
    budget = Budget()
    assert budget.check_tool_calls(done=10**6, asking=10**6) is None
    assert budget.check_time_and_cost(seconds=10**9, usage=log_of((10**9, 10**9))) is None


def test_the_limit_itself_is_allowed_and_one_more_is_not() -> None:
    budget = Budget(max_tool_calls=3, max_seconds=10.0, max_cost_usd=5.0, pricing=DOLLAR_PER_TOKEN)
    assert budget.check_tool_calls(done=2, asking=1) is None
    assert "tool calls" in (budget.check_tool_calls(done=2, asking=2) or "")
    assert budget.check_time_and_cost(seconds=10.0, usage=log_of((3, 2))) is None
    assert "seconds" in (budget.check_time_and_cost(seconds=10.1, usage=log_of()) or "")
    assert "cost" in (budget.check_time_and_cost(seconds=0.0, usage=log_of((3, 3))) or "")


def test_the_reason_gives_the_number_that_was_used_and_the_limit() -> None:
    reason = Budget(max_tool_calls=3).check_tool_calls(done=3, asking=1)
    assert reason is not None and "4" in reason and "3" in reason


# --- In a run --------------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SlowTool(EchoTool):
    """A tool that takes `seconds` of the (fake) time."""

    name = "echo"

    def __init__(self, clock: Clock, seconds: float) -> None:
        super().__init__()
        self._clock, self._seconds = clock, seconds

    def run(self, args: EchoArgs, ctx: RunContext) -> ToolResult:
        self._clock.now += self._seconds
        return super().run(args, ctx)


def echo_call(call_id: str) -> Any:
    return tool_use_message((call_id, "echo", {"text": "hi"}))


def go(
    settings: Settings,
    tmp_path: Path,
    replies: list[Any],
    budget: Budget | None,
    tool: EchoTool | None = None,
    clock: Clock | None = None,
) -> tuple[AgentResult, FakeLLM, EchoTool]:
    echo = tool or EchoTool()
    llm = FakeLLM(replies)
    result = run_agent(
        "the task",
        llm=llm,
        tools=make_tool_registry(echo),
        ctx=RunContext(workspace=tmp_path, strict=True),
        settings=settings,
        system="s",
        budget=budget,
        clock=clock or Clock(),
    )
    return result, llm, echo


def test_a_run_inside_its_budget_is_the_same_as_a_run_without_one(settings: Settings, tmp_path: Path) -> None:
    replies = [echo_call("a"), text_message("done")]
    big = Budget(max_tool_calls=5, max_seconds=100.0, max_cost_usd=100.0, pricing=DOLLAR_PER_TOKEN)
    with_budget, _, _ = go(settings, tmp_path, replies, big)
    plain, _, _ = go(settings, tmp_path, [echo_call("a"), text_message("done")], None)
    assert (with_budget.status, with_budget.iterations) == (plain.status, plain.iterations) == ("done", 2)


def test_the_tool_call_over_the_limit_is_not_run_and_the_run_ends_with_status_budget(
    settings: Settings, tmp_path: Path
) -> None:
    replies = [echo_call("a"), echo_call("b"), echo_call("c")]
    result, llm, echo = go(settings, tmp_path, replies, Budget(max_tool_calls=2))
    assert result.status == "budget" and "tool calls" in result.detail
    assert echo.calls == 2 and len(llm.messages.calls) == 3  # the third was asked for and not run


def test_a_reply_that_would_go_over_is_not_run_partly(settings: Settings, tmp_path: Path) -> None:
    three_at_once = tool_use_message(
        ("a", "echo", {"text": "1"}), ("b", "echo", {"text": "2"}), ("c", "echo", {"text": "3"})
    )
    result, _, echo = go(settings, tmp_path, [three_at_once], Budget(max_tool_calls=2))
    assert result.status == "budget" and echo.calls == 0


def test_exactly_as_many_calls_as_the_limit_is_fine(settings: Settings, tmp_path: Path) -> None:
    result, _, echo = go(
        settings, tmp_path, [echo_call("a"), echo_call("b"), text_message("ok")], Budget(max_tool_calls=2)
    )
    assert result.status == "done" and echo.calls == 2


def test_time_over_the_limit_stops_the_run_before_the_next_model_call(
    settings: Settings, tmp_path: Path
) -> None:
    clock = Clock()
    result, llm, _ = go(
        settings, tmp_path, [echo_call("a"), text_message("never asked")], Budget(max_seconds=20.0),
        tool=SlowTool(clock, 30.0), clock=clock,
    )  # fmt: skip
    assert result.status == "budget" and "seconds" in result.detail
    assert len(llm.messages.calls) == 1


def test_money_over_the_limit_stops_the_run_before_the_next_model_call(
    settings: Settings, tmp_path: Path
) -> None:
    expensive = tool_use_message(("a", "echo", {"text": "hi"}), usage=(60, 50))  # $110 at $1 per token
    budget = Budget(max_cost_usd=100.0, pricing=DOLLAR_PER_TOKEN)
    result, llm, _ = go(settings, tmp_path, [expensive, text_message("never asked")], budget)
    assert result.status == "budget" and "cost" in result.detail
    assert len(llm.messages.calls) == 1


def test_the_end_of_the_run_is_in_the_log_as_budget(settings: Settings, tmp_path: Path) -> None:
    from prova.harness.redact import Redactor
    from prova.harness.trajectory import TrajectoryWriter
    from tests.unit.agent.test_loop_trajectory import DatetimeClock

    log = tmp_path / "t.jsonl"
    writer = TrajectoryWriter(log, run_id="r", clock=DatetimeClock(), redactor=Redactor())
    result = run_agent(
        "the task", llm=FakeLLM([echo_call("a"), echo_call("b")]), tools=make_tool_registry(EchoTool()),
        ctx=RunContext(workspace=tmp_path, strict=True), settings=settings, system="s",
        budget=Budget(max_tool_calls=1), trajectory=writer,
    )  # fmt: skip
    last = log.read_text(encoding="utf-8").splitlines()[-1]
    assert result.status == "budget" and '"status":"budget"' in last.replace(" ", "")
