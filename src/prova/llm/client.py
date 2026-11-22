"""The LLM client interface (a Protocol) and the factory that builds the real Anthropic client."""

from collections.abc import Sequence
from typing import Protocol

import anthropic
from anthropic.types import Message, MessageParam

from prova.config import Settings


class MessagesAPI(Protocol):
    """The part of `client.messages` we actually use."""

    def create(self, *, model: str, max_tokens: int, messages: Sequence[MessageParam]) -> Message: ...

    # TODO(M4): add stream() and system/tools params once the agent loop lives in src/.
    # TODO(M23): add count_tokens().


class LLMClient(Protocol):
    """What the rest of Prova needs from a client. A test fake only has to provide this shape."""

    @property
    def messages(self) -> MessagesAPI: ...


def make_client(settings: Settings) -> LLMClient:
    """Build the real client. Called from build_app only, never at import time."""
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
