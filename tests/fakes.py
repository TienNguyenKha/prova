"""Hand-written fakes. They satisfy the Protocols in src/ by shape, with no inheritance and no patching."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from anthropic import Omit, omit
from anthropic.types import Message, MessageParam
from pydantic import BaseModel, ConfigDict

from prova.errors import ErrorCode, ToolError
from prova.registry import Registry
from prova.tools.base import Permission, RunContext, Tool, ToolResult
from prova.tools.registry import ToolRegistry

# --- Canned model replies ----------------------------------------------------


def build_message(
    content: list[dict[str, object]], stop_reason: str, stop_details: Mapping[str, object] | None = None
) -> Message:
    """Build a canned assistant Message, the way the real API would return it."""
    return Message.model_validate(
        {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": "fake-model",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def text_message(
    text: str, *, stop_reason: str = "end_turn", stop_details: Mapping[str, object] | None = None
) -> Message:
    return build_message([{"type": "text", "text": text}], stop_reason, stop_details)


def tool_use_message(
    *calls: tuple[str, str, Mapping[str, object]], text: str | None = None, stop_reason: str = "tool_use"
) -> Message:
    """Build a canned assistant Message that calls tools. Each call is (tool_use_id, name, input)."""
    content: list[dict[str, object]] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    content += [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in calls]
    return build_message(content, stop_reason)


# --- A fake LLM client -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Call:
    """One recorded call to messages.create, so tests can assert on what was sent."""

    model: str
    max_tokens: int
    messages: Sequence[MessageParam]
    system: str | None = None
    output_config: Any = None
    tools: Any = None


@dataclass
class FakeMessages:
    responses: list[Message | Exception]  # an Exception in the list is raised instead of returned
    calls: list[Call] = field(default_factory=list[Call])

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: Sequence[MessageParam],
        system: str | Omit = omit,
        output_config: Any = omit,
        tools: Any = omit,
    ) -> Message:
        self.calls.append(
            Call(
                model,
                max_tokens,
                list(messages),  # a copy: the agent loop keeps appending to its own list after the call
                system=None if isinstance(system, Omit) else system,
                output_config=None if isinstance(output_config, Omit) else output_config,
                tools=None if isinstance(tools, Omit) else tools,
            )
        )
        if not self.responses:
            raise AssertionError("FakeLLM has no canned responses left (unexpected extra call)")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeLLM:
    """Returns the canned responses in order. Satisfies LLMClient."""

    def __init__(self, responses: Sequence[Message | Exception] = ()) -> None:
        self.messages = FakeMessages(list(responses))


# --- Fake tools: any object with the right shape is a Tool (no inheritance) --


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    times: int = 1


class EchoTool:
    name = "echo"
    description = "Repeat a text."
    args_model = EchoArgs
    permission = Permission.READ

    def __init__(self) -> None:
        self.calls = 0

    def run(self, args: EchoArgs, ctx: RunContext) -> ToolResult:
        self.calls += 1
        return ToolResult.ok(args.text * args.times)


class FailingTool:
    name = "failing"
    description = "Always reports a problem the model can fix."
    args_model = EchoArgs
    permission = Permission.READ

    def run(self, args: EchoArgs, ctx: RunContext) -> ToolResult:
        raise ToolError(ErrorCode.NOT_FOUND, "no such thing: " + args.text)


class BuggyTool:
    name = "buggy"
    description = "Has a bug."
    args_model = EchoArgs
    permission = Permission.READ

    def run(self, args: EchoArgs, ctx: RunContext) -> ToolResult:
        raise RuntimeError("secret-detail")


class WriterTool:
    name = "write_thing"
    description = "Needs write access."
    args_model = EchoArgs
    permission = Permission.WRITE

    def __init__(self) -> None:
        self.ran = 0

    def run(self, args: EchoArgs, ctx: RunContext) -> ToolResult:
        self.ran += 1
        return ToolResult.ok("written")


def make_tool_registry(*tools: Tool) -> ToolRegistry:
    registry = Registry[Tool]("tool")
    for tool in tools:
        registry.register(tool)
    registry.freeze()
    return ToolRegistry(registry)
