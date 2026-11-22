"""Tests for prova.llm.stop: describe why a reply ended, in words that are safe to show or log."""

from anthropic.types import Message

from prova.llm.stop import describe_refusal
from tests.fakes import text_message


def refusal(**details: str) -> Message:
    return text_message("no", stop_reason="refusal", stop_details={"type": "refusal", **details})


def test_names_the_category_and_the_explanation() -> None:
    message = refusal(category="cyber", explanation="looks like exploit work")
    assert describe_refusal(message) == "the model refused: cyber, looks like exploit work"


def test_names_only_what_is_known() -> None:
    assert describe_refusal(refusal(category="cyber")) == "the model refused: cyber"
    assert describe_refusal(refusal(explanation="because")) == "the model refused: because"


def test_says_only_that_the_model_refused_when_there_are_no_details() -> None:
    assert describe_refusal(refusal()) == "the model refused"
    assert describe_refusal(text_message("no", stop_reason="refusal")) == "the model refused"
