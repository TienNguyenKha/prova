"""Tests for prova.structured: read structured output, and fail closed on anything suspicious."""

import json
from typing import Any

import pytest
from pydantic import BaseModel, Field, ValidationError

from prova.config import Settings
from prova.errors import StructuredErrorKind as Kind
from prova.llm.usage import UsageLog, UsageRecord
from prova.structured import (
    BugClassification,
    StructuredError,
    check_stop_reason,
    extract,
    parse_structured,
)
from tests.fakes import FakeLLM, build_message, text_message

VALID: dict[str, Any] = {
    "severity": "major",
    "category": "security",
    "confidence": 0.9,
    "reason": "shell=True with user input",
}
SYSTEM = "You classify bugs."
USER = "<code_to_classify>os.system(cmd)</code_to_classify>"


def run(llm: FakeLLM, settings: Settings) -> BugClassification:
    return extract(llm, BugClassification, system=SYSTEM, user=USER, settings=settings)


def reply(payload: dict[str, Any]) -> FakeLLM:
    return FakeLLM([text_message(json.dumps(payload))])


# --- extract: success path --------------------------------------------------


def test_valid_json_becomes_a_validated_model(settings: Settings) -> None:
    result = run(reply(VALID), settings)
    assert result == BugClassification(**VALID)


def test_request_carries_model_system_user_and_json_schema(settings: Settings) -> None:
    llm = reply(VALID)
    run(llm, settings)

    (call,) = llm.messages.calls
    assert call.model == settings.model
    assert call.max_tokens == settings.max_tokens
    assert call.system == SYSTEM
    assert call.messages == [{"role": "user", "content": USER}]
    fmt = call.output_config["format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["schema"]["properties"]) == set(VALID)


# --- check_stop_reason: is the reply complete? ------------------------------


def test_end_turn_passes_the_stop_reason_check() -> None:
    check_stop_reason(text_message("{}"))  # must not raise


def test_truncated_response_is_an_error_even_if_a_prefix_looks_fine() -> None:
    message = text_message('{"severity": "maj', stop_reason="max_tokens")
    with pytest.raises(StructuredError, match="truncated") as exc:
        check_stop_reason(message)
    assert exc.value.kind == Kind.TRUNCATED


def test_refusal_is_an_error_and_explains_why() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "looks like exploit work"}
    message = text_message("I can't help with that.", stop_reason="refusal", stop_details=details)
    with pytest.raises(StructuredError, match="refus") as exc:
        check_stop_reason(message)
    assert exc.value.kind == Kind.REFUSED
    assert "cyber" in str(exc.value)


@pytest.mark.parametrize("stop_reason", ["tool_use", "pause_turn", "model_context_window_exceeded"])
def test_any_other_stop_reason_is_an_error(stop_reason: str) -> None:
    with pytest.raises(StructuredError, match="stop_reason") as exc:
        check_stop_reason(text_message(json.dumps(VALID), stop_reason=stop_reason))
    assert exc.value.kind == Kind.UNEXPECTED_STOP


def test_extract_checks_the_stop_reason_before_reading_the_text(settings: Settings) -> None:
    llm = FakeLLM([text_message(json.dumps(VALID), stop_reason="max_tokens")])  # valid JSON, but cut off
    with pytest.raises(StructuredError) as exc:
        run(llm, settings)
    assert exc.value.kind == Kind.TRUNCATED


def test_response_without_a_text_block_is_an_error(settings: Settings) -> None:
    llm = FakeLLM([build_message([], "end_turn")])
    with pytest.raises(StructuredError, match="no text") as exc:
        run(llm, settings)
    assert exc.value.kind == Kind.NO_TEXT


# --- parse_structured: is the text valid output? ----------------------------

INVALID_TEXTS: dict[str, tuple[str, Kind]] = {
    "not json": ("I think this is a major security bug.", Kind.INVALID_JSON),
    "markdown fence": ("```json\n" + json.dumps(VALID) + "\n```", Kind.INVALID_JSON),
    "missing field": (json.dumps({k: v for k, v in VALID.items() if k != "reason"}), Kind.MISSING_FIELD),
    "bad enum value": (json.dumps({**VALID, "severity": "catastrophic"}), Kind.OUT_OF_ENUM),
    "confidence out of range": (json.dumps({**VALID, "confidence": 1.5}), Kind.OUT_OF_RANGE),
    "extra field": (json.dumps({**VALID, "owner": "someone"}), Kind.EXTRA_FIELD),
    "wrong type": (json.dumps({**VALID, "confidence": "high"}), Kind.WRONG_TYPE),
}


def test_parse_structured_returns_the_model_for_valid_text() -> None:
    assert parse_structured(BugClassification, json.dumps(VALID)) == BugClassification(**VALID)


@pytest.mark.parametrize(("text", "kind"), INVALID_TEXTS.values(), ids=INVALID_TEXTS.keys())
def test_invalid_text_has_the_right_kind_and_keeps_the_original_cause(text: str, kind: Kind) -> None:
    with pytest.raises(StructuredError) as exc:
        parse_structured(BugClassification, text)
    assert exc.value.kind == kind
    assert isinstance(exc.value.__cause__, ValidationError)  # `raise ... from e` keeps the Pydantic detail


def test_extract_reports_invalid_text_through_the_same_kinds(settings: Settings) -> None:
    text, kind = INVALID_TEXTS["bad enum value"]
    with pytest.raises(StructuredError) as exc:
        run(FakeLLM([text_message(text)]), settings)
    assert exc.value.kind == kind


@pytest.mark.parametrize(
    ("key", "name"),
    [("missing field", "reason"), ("bad enum value", "severity"), ("confidence out of range", "confidence")],
)
def test_error_message_names_the_offending_field(key: str, name: str) -> None:
    text, _ = INVALID_TEXTS[key]
    with pytest.raises(StructuredError, match=name):
        parse_structured(BugClassification, text)


def test_error_message_does_not_echo_the_models_output() -> None:
    leak_marker = "should-never-appear-in-an-error"
    text = json.dumps({**VALID, "severity": leak_marker})
    with pytest.raises(StructuredError) as exc:
        parse_structured(BugClassification, text)
    assert leak_marker not in str(exc.value)


def test_with_several_problems_the_most_basic_one_decides_the_kind() -> None:
    # Missing fields, a bad enum value and an out-of-range number at once: the report is MISSING_FIELD.
    text = json.dumps({"severity": "catastrophic", "confidence": 5})
    with pytest.raises(StructuredError) as exc:
        parse_structured(BugClassification, text)
    assert exc.value.kind == Kind.MISSING_FIELD


def test_a_rule_without_a_dedicated_kind_is_reported_as_other() -> None:
    class Note(BaseModel):
        title: str = Field(min_length=3)

    with pytest.raises(StructuredError) as exc:
        parse_structured(Note, '{"title": "x"}')
    assert exc.value.kind == Kind.OTHER


# TODO(M21): the plan also asks for a caller test ("on StructuredError the guardrail blocks"); there is no
# guardrail in Prova yet, so that test belongs with the first real caller.


# --- what a call costs ---------------------------------------------------------------------------------


def test_the_usage_of_the_call_is_added_to_a_log_that_is_handed_in(settings: Settings) -> None:
    log = UsageLog()
    log.add(UsageRecord(turn=1, input_tokens=5, output_tokens=5))
    llm = FakeLLM([text_message(json.dumps(VALID), usage=(100, 20))])
    extract(llm, BugClassification, system=SYSTEM, user=USER, settings=settings, usage=log)
    assert [(r.turn, r.input_tokens, r.output_tokens) for r in log.records] == [(1, 5, 5), (2, 100, 20)]


@pytest.mark.parametrize(
    "message",
    [
        text_message(json.dumps(VALID), stop_reason="max_tokens", usage=(100, 20)),
        text_message("not json", usage=(100, 20)),
    ],
)
def test_a_call_that_fails_is_still_paid_for_and_counted(settings: Settings, message: Any) -> None:
    log = UsageLog()
    with pytest.raises(StructuredError):
        extract(FakeLLM([message]), BugClassification, system=SYSTEM, user=USER, settings=settings, usage=log)
    assert log.total_input_tokens == 100 and log.total_output_tokens == 20
