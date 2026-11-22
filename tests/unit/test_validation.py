"""Tests for prova.validation: turn a Pydantic ValidationError into a short message for a model or a log."""

from pydantic import BaseModel, ValidationError

from prova.validation import describe_errors


class Inner(BaseModel):
    count: int


class Outer(BaseModel):
    path: str
    inner: Inner | None = None


def failure(data: object) -> ValidationError:
    try:
        Outer.model_validate(data)
    except ValidationError as e:
        return e
    raise AssertionError("expected a ValidationError")


def test_names_the_field_and_the_rule() -> None:
    assert describe_errors(failure({})) == "path: Field required"


def test_several_problems_are_joined_in_order() -> None:
    message = describe_errors(failure({"inner": {"count": "x"}}))
    assert message.startswith("path: Field required; ")
    assert "inner.count" in message  # nested locations are dotted


def test_a_problem_with_the_whole_document_is_named_root() -> None:
    assert describe_errors(failure("not an object")).startswith("<root>: ")


def test_it_does_not_echo_the_offending_value() -> None:
    message = describe_errors(failure({"path": "ok", "inner": {"count": "TOP-SECRET-VALUE"}}))
    assert "TOP-SECRET-VALUE" not in message
