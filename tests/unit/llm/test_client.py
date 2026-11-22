"""Tests for prova.llm.client.AnthropicClient: same calls as the SDK, but SDK errors become LLMError."""

from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from prova.config import Settings
from prova.errors import LLMError
from prova.llm.client import AnthropicClient, LLMClient, make_client
from tests.fakes import FakeLLM, text_message

REQUEST = httpx2.Request("POST", "https://api.example.test/v1/messages")


def status_error(code: int) -> anthropic.APIStatusError:
    response = httpx2.Response(code, request=REQUEST)
    return anthropic.APIStatusError("boom", response=response, body={"error": {"message": "boom"}})


class RaisingMessages:
    """The inner `messages` API of a client whose every call fails with the given exception."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def create(self, **kwargs: Any) -> Message:
        raise self._error


class RaisingLLM:
    def __init__(self, error: BaseException) -> None:
        self.messages = RaisingMessages(error)


def call(client: LLMClient) -> Message:
    return client.messages.create(model="m", max_tokens=1, messages=[{"role": "user", "content": "hi"}])


# --- It is a pass-through when nothing goes wrong ---------------------------


def test_arguments_and_the_reply_pass_through_unchanged() -> None:
    reply = text_message("hello")
    inner = FakeLLM([reply])
    tools: Any = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]

    result = AnthropicClient(inner).messages.create(
        model="m",
        max_tokens=7,
        messages=[{"role": "user", "content": "hi"}],
        system="be brief",
        tools=tools,
    )

    assert result is reply
    (seen,) = inner.messages.calls
    assert (seen.model, seen.max_tokens, seen.system) == ("m", 7, "be brief")
    assert seen.tools == tools


def test_optional_arguments_that_were_not_given_stay_not_given() -> None:
    inner = FakeLLM([text_message("hello")])
    call(AnthropicClient(inner))
    (seen,) = inner.messages.calls
    assert seen.system is None and seen.tools is None and seen.output_config is None


# --- SDK errors become LLMError, with the cause kept ------------------------


@pytest.mark.parametrize("code", [400, 401, 404, 429, 500, 503])
def test_a_status_error_becomes_an_llm_error_that_keeps_the_status_code(code: int) -> None:
    original = status_error(code)
    with pytest.raises(LLMError, match="boom") as exc:
        call(AnthropicClient(RaisingLLM(original)))
    assert exc.value.status_code == code
    assert exc.value.__cause__ is original  # `raise ... from e` keeps the SDK exception for debugging


@pytest.mark.parametrize(
    "error", [anthropic.APIConnectionError(request=REQUEST), anthropic.APITimeoutError(request=REQUEST)]
)
def test_a_connection_error_or_timeout_becomes_an_llm_error_without_a_status_code(error: Exception) -> None:
    with pytest.raises(LLMError) as exc:
        call(AnthropicClient(RaisingLLM(error)))
    assert exc.value.status_code is None
    assert exc.value.__cause__ is error


def test_a_bug_of_ours_is_not_wrapped() -> None:
    # A TypeError is not an API problem: hiding it behind LLMError would make it look like a network failure.
    with pytest.raises(TypeError, match="bad argument"):
        call(AnthropicClient(RaisingLLM(TypeError("bad argument"))))


# --- Wiring ------------------------------------------------------------------


def test_make_client_returns_the_wrapped_client_and_never_touches_the_network(settings: Settings) -> None:
    assert isinstance(make_client(settings), AnthropicClient)
