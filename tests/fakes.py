"""Hand-written fakes. They satisfy the Protocols in src/ by shape, with no inheritance and no patching."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from anthropic.types import Message, MessageParam


def text_message(text: str, *, stop_reason: str = "end_turn") -> Message:
    """Build a canned assistant Message, the way the real API would return it."""
    return Message.model_validate(
        {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": "fake-model",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


@dataclass(frozen=True, slots=True)
class Call:
    """One recorded call to messages.create, so tests can assert on what was sent."""

    model: str
    max_tokens: int
    messages: Sequence[MessageParam]


@dataclass
class FakeMessages:
    responses: list[Message]
    calls: list[Call] = field(default_factory=list[Call])

    def create(self, *, model: str, max_tokens: int, messages: Sequence[MessageParam]) -> Message:
        self.calls.append(Call(model, max_tokens, messages))
        if not self.responses:
            raise AssertionError("FakeLLM has no canned responses left (unexpected extra call)")
        return self.responses.pop(0)


class FakeLLM:
    """Returns the canned responses in order. Satisfies LLMClient."""

    def __init__(self, responses: Sequence[Message] = ()) -> None:
        self.messages = FakeMessages(list(responses))
