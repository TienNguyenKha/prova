"""Hand-written fakes. They satisfy the Protocols in src/ by shape, with no inheritance and no patching."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from anthropic import Omit, omit
from anthropic.types import Message, MessageParam


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


def tool_use_message(*calls: tuple[str, str, dict[str, object]], text: str | None = None) -> Message:
    """Build a canned assistant Message that calls tools. Each call is (tool_use_id, name, input)."""
    content: list[dict[str, object]] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    content += [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in calls]
    return build_message(content, "tool_use")


@dataclass(frozen=True, slots=True)
class Call:
    """One recorded call to messages.create, so tests can assert on what was sent."""

    model: str
    max_tokens: int
    messages: Sequence[MessageParam]
    system: str | None = None
    output_config: Any = None


@dataclass
class FakeMessages:
    responses: list[Message]
    calls: list[Call] = field(default_factory=list[Call])

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: Sequence[MessageParam],
        system: str | Omit = omit,
        output_config: Any = omit,
    ) -> Message:
        self.calls.append(
            Call(
                model,
                max_tokens,
                messages,
                system=None if isinstance(system, Omit) else system,
                output_config=None if isinstance(output_config, Omit) else output_config,
            )
        )
        if not self.responses:
            raise AssertionError("FakeLLM has no canned responses left (unexpected extra call)")
        return self.responses.pop(0)


class FakeLLM:
    """Returns the canned responses in order. Satisfies LLMClient."""

    def __init__(self, responses: Sequence[Message] = ()) -> None:
        self.messages = FakeMessages(list(responses))
