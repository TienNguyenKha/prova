"""The ReAct loop: ask the model, run the tools it asks for, send the results back, repeat."""

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, cast

from anthropic import omit
from anthropic.types import ContentBlockParam, Message, MessageParam, ToolResultBlockParam

from prova.config import Settings
from prova.errors import LLMError
from prova.llm.client import LLMClient
from prova.llm.stop import describe_refusal
from prova.llm.usage import UsageLog, UsageRecord
from prova.tools.base import RunContext
from prova.tools.registry import ToolRegistry

Status = Literal["done", "max_iterations", "max_tokens", "refused", "error"]


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: Status
    # The text of the model's last reply: complete when "done", partial when "max_tokens", the refusal when
    # "refused", and empty when the run ended without a usable reply.
    final_text: str
    iterations: int  # how many times the model was called (the last call may have failed)
    usage: UsageLog  # one record per model call that returned
    messages: tuple[MessageParam, ...]  # the whole conversation, for logs and tests
    detail: str = ""  # why the run did not end normally


def run_agent(
    task: str,
    *,
    llm: LLMClient,
    tools: ToolRegistry,
    ctx: RunContext,
    settings: Settings,
    system: str,
    max_iterations: int = 30,
    clock: Callable[[], float] = time.perf_counter,
) -> AgentResult:
    """Run the model on `task` until it answers, fails, or has used `max_iterations` calls.

    Which tools the model is offered, and which it may actually run, both come from `ctx.permissions`.
    """
    messages: list[MessageParam] = [{"role": "user", "content": task}]
    offered = tools.to_api_tools(ctx.permissions)
    usage = UsageLog()

    def finish(status: Status, iterations: int, *, final_text: str = "", detail: str = "") -> AgentResult:
        return AgentResult(status, final_text, iterations, usage, tuple(messages), detail)

    for turn in range(1, max_iterations + 1):
        started = clock()
        try:
            response = llm.messages.create(
                model=settings.model,
                max_tokens=settings.max_tokens,
                messages=messages,
                system=system,
                tools=offered or omit,
            )
        except LLMError as e:
            code = f" (HTTP {e.status_code})" if e.status_code is not None else ""
            return finish("error", turn, detail=f"{e}{code}")
        usage.add(_usage_record(turn, response, clock() - started))
        # The SDK accepts the reply's blocks as they are; the tool_use blocks MUST stay in the history.
        messages.append({"role": "assistant", "content": cast(Iterable[ContentBlockParam], response.content)})

        reason = response.stop_reason
        if reason == "end_turn":
            return finish("done", turn, final_text=_text_of(response))
        if reason == "tool_use":
            messages.append({"role": "user", "content": _run_tools(response, tools, ctx)})
            continue
        # Everything below ends the run, and no tool in such a reply is run: the reply is not a complete,
        # trustworthy request. A cut-off call may have incomplete arguments.
        if reason == "max_tokens":
            detail = "the reply was cut at max_tokens; any tool call in it was not run"
            return finish("max_tokens", turn, final_text=_text_of(response), detail=detail)
        if reason == "refusal":
            return finish("refused", turn, final_text=_text_of(response), detail=describe_refusal(response))
        # pause_turn, stop_sequence, model_context_window_exceeded, a value added later, or None: we cannot
        # handle them correctly yet, and guessing would mean looping blindly.
        return finish("error", turn, detail=f"unexpected stop_reason {reason!r}")

    return finish("max_iterations", max_iterations)


def _run_tools(response: Message, tools: ToolRegistry, ctx: RunContext) -> list[ToolResultBlockParam]:
    """Answer every tool_use block of the reply. All results go back in ONE message, in the same order."""
    return [
        tools.dispatch(block.name, block.input, ctx).to_tool_result_block(block.id)
        for block in response.content
        if block.type == "tool_use"
    ]


def _text_of(response: Message) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


def _usage_record(turn: int, response: Message, latency_s: float) -> UsageRecord:
    usage = response.usage
    return UsageRecord(
        turn=turn,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        latency_s=latency_s,
    )
