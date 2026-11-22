"""The LLM client interface (a Protocol), our client on top of the Anthropic SDK, and the factory for it."""

from collections.abc import Sequence
from typing import Protocol

import anthropic
from anthropic import Omit, omit
from anthropic.types import Message, MessageParam, OutputConfigParam, ToolParam

from prova.config import Settings
from prova.errors import LLMError


class MessagesAPI(Protocol):
    """The part of `client.messages` we actually use."""

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: Sequence[MessageParam],
        system: str | Omit = omit,
        output_config: OutputConfigParam | Omit = omit,
        tools: Sequence[ToolParam] | Omit = omit,
    ) -> Message: ...

    # TODO(M23): add count_tokens(). TODO(M24): add stream() once a service has to stream to a user.


class LLMClient(Protocol):
    """What the rest of Prova needs from a client. A test fake only has to provide this shape."""

    @property
    def messages(self) -> MessagesAPI: ...


class _GuardedMessages:
    """`messages.create` with SDK errors turned into LLMError. Anything else is passed through untouched."""

    def __init__(self, inner: MessagesAPI) -> None:
        self._inner = inner

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: Sequence[MessageParam],
        system: str | Omit = omit,
        output_config: OutputConfigParam | Omit = omit,
        tools: Sequence[ToolParam] | Omit = omit,
    ) -> Message:
        try:
            return self._inner.create(
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                output_config=output_config,
                tools=tools,
            )
        except anthropic.APIError as e:
            # Only API problems are wrapped: a TypeError would be our bug, not a network failure.
            status = e.status_code if isinstance(e, anthropic.APIStatusError) else None
            raise LLMError(f"{type(e).__name__}: {e.message}", status_code=status) from e


class AnthropicClient:
    """Our LLMClient on top of the Anthropic SDK: the same calls, but SDK errors become LLMError.

    The SDK already retries connection errors, 408, 409, 429 and 5xx twice, so this class does not retry:
    an LLMError means the SDK has already given up.
    """

    def __init__(self, inner: LLMClient) -> None:
        self.messages: MessagesAPI = _GuardedMessages(inner.messages)


def make_client(settings: Settings) -> LLMClient:
    """Build the real client. Called from build_app only, never at import time."""
    sdk = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    return AnthropicClient(sdk)
