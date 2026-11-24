"""The ReAct loop: ask the model, run the tools it asks for, send the results back, repeat."""

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, cast

from anthropic import omit
from anthropic.types import ContentBlockParam, Message, MessageParam, ToolResultBlockParam

from prova.config import Settings
from prova.errors import ErrorCode, LLMError
from prova.harness.hooks import HookEvent, HookKind, HookOutcome, Hooks, run_hooks
from prova.harness.redact import Redactor
from prova.harness.trajectory import (
    HookDecisionEvent,
    ModelRequestEvent,
    ModelResponseEvent,
    OutcomeEvent,
    TaskEvent,
    ToolCallEvent,
    ToolResultEvent,
    TrajectoryEvent,
    TrajectoryWriter,
)
from prova.llm.client import LLMClient
from prova.llm.stop import describe_refusal
from prova.llm.usage import UsageLog, UsageRecord
from prova.tools.base import RunContext, ToolResult
from prova.tools.registry import ToolRegistry

# "unverified": the model said it was done, but the stop hooks did not agree, even after being asked again.
Status = Literal["done", "unverified", "max_iterations", "max_tokens", "refused", "error"]


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
    trajectory: TrajectoryWriter | None = None,
    redactor: Redactor | None = None,
    hooks: Hooks | None = None,
    prompt_hash: str | None = None,
) -> AgentResult:
    """Run the model on `task` until it answers, fails, or has used `max_iterations` calls.

    Which tools the model is offered, and which it may actually run, both come from `ctx.permissions`.
    With a `trajectory`, every step is logged; a log that cannot be written stops the run (TrajectoryError).
    The `redactor` cleans tool results before the model sees them (the trajectory redacts on its own).
    `hooks` are rules around the loop (see prova.harness.hooks): they can block a tool, add notes to its
    result, or keep the run going when the model wants to stop. Permissions are not hooks: no hook can
    lift them. `prompt_hash` (see prova.prompts.render) is written into the first line of the log, so a result
    can be tied to the prompt it ran with.
    """
    redact = redactor if redactor is not None else Redactor()
    active = hooks if hooks is not None else Hooks()
    stop_denials = 0
    messages: list[MessageParam] = [{"role": "user", "content": task}]
    offered = tools.to_api_tools(ctx.permissions)
    usage = UsageLog()

    def log(event: TrajectoryEvent) -> None:
        if trajectory is not None:
            trajectory.write(event)

    def finish(status: Status, iterations: int, *, final_text: str = "", detail: str = "") -> AgentResult:
        log(
            OutcomeEvent(
                status=status,
                iterations=iterations,
                detail=detail,
                input_tokens=usage.total_input_tokens,
                output_tokens=usage.total_output_tokens,
            )
        )
        return AgentResult(status, final_text, iterations, usage, tuple(messages), detail)

    # Logged BEFORE the first model call: if the log cannot be written, no model call goes unlogged.
    log(TaskEvent(task=task, prompt_hash=prompt_hash))  # built even with no log: a bad hash stops here

    for turn in range(1, max_iterations + 1):
        log(
            ModelRequestEvent(
                turn=turn, model=settings.model, messages=len(messages), tools=[t["name"] for t in offered]
            )
        )
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
        log(
            ModelResponseEvent(
                turn=turn,
                stop_reason=response.stop_reason,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                text=_text_of(response),
                tool_calls=sum(1 for block in response.content if block.type == "tool_use"),
            )
        )
        # The SDK accepts the reply's blocks as they are; the tool_use blocks MUST stay in the history.
        messages.append({"role": "assistant", "content": cast(Iterable[ContentBlockParam], response.content)})

        reason = response.stop_reason
        if reason == "end_turn":
            verdict = run_hooks(active.stop, HookEvent("stop", ctx))
            _log_hook_outcomes(log, "stop", None, verdict.outcomes)
            if not verdict.blocked:
                return finish("done", turn, final_text=_text_of(response))
            if stop_denials >= active.max_stop_denials:
                detail = (
                    f"the stop checks still refused after {stop_denials} more tries, so the run is not "
                    f"called done; last reason: {verdict.message}"
                )
                return finish("unverified", turn, final_text=_text_of(response), detail=detail)
            stop_denials += 1
            retry = f"[stop check] You may not finish yet. {verdict.message}"
            messages.append({"role": "user", "content": retry})
            continue
        if reason == "tool_use":
            answers = _answer_tool_calls(response, tools, ctx, redact, log, active)
            messages.append({"role": "user", "content": answers})
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


def _answer_tool_calls(
    response: Message,
    tools: ToolRegistry,
    ctx: RunContext,
    redact: Redactor,
    log: Callable[[TrajectoryEvent], None],
    hooks: Hooks,
) -> list[ToolResultBlockParam]:
    """Answer every tool_use block of the reply. All results go back in ONE message, in the same order."""
    answers: list[ToolResultBlockParam] = []
    for block in response.content:
        if block.type != "tool_use":
            continue
        denied = tools.is_denied(block.name, ctx)  # the permissions' decision, not a hook's
        args = _as_object(block.input)
        # Logged BEFORE anything runs: if a hook or the tool hangs, the log still shows what was asked.
        log(
            ToolCallEvent(
                tool=block.name,
                tool_use_id=block.id,
                input=args,
                decision="deny" if denied else "allow",
            )
        )
        before = run_hooks(hooks.pre, HookEvent("pre_tool_use", ctx, block.name, args))
        _log_hook_outcomes(log, "pre_tool_use", block.name, before.outcomes)
        if before.blocked:
            result = ToolResult.error(ErrorCode.SAFETY, before.message)  # it did not run: no post hooks
        else:
            result = tools.dispatch(block.name, block.input, ctx)  # a hook's "allow" lifts no denial
            after = run_hooks(hooks.post, HookEvent("post_tool_use", ctx, block.name, args, result))
            _log_hook_outcomes(log, "post_tool_use", block.name, after.outcomes)
            result = result.with_notes(*after.notes)
        answers.append(result.to_tool_result_block(block.id, redact=redact.redact))
        log(
            ToolResultEvent(
                tool=block.name,
                tool_use_id=block.id,
                success=result.success,
                error_code=result.error_code.value if result.error_code is not None else None,
                message=result.message,
                chars=len(redact.redact(result.model_text())),  # how much the model was actually shown
            )
        )
    return answers


def _log_hook_outcomes(
    log: Callable[[TrajectoryEvent], None],
    kind: HookKind,
    tool: str | None,
    outcomes: tuple[HookOutcome, ...],
) -> None:
    for outcome in outcomes:
        log(
            HookDecisionEvent(
                hook_kind=kind,
                hook=outcome.hook,
                tool=tool,
                action=outcome.action,
                message=outcome.message,
                error=outcome.error,
            )
        )


def _as_object(value: object) -> dict[str, Any]:
    """The input of a tool call should be an object; if the model sent something else, log it as text."""
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {"_not_an_object": repr(value)}


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
