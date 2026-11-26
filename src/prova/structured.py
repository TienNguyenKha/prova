"""Structured output: ask the API for JSON that matches a Pydantic model, then trust nothing it returns."""

from typing import Literal

from anthropic import transform_schema
from anthropic.types import Message
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prova.config import Settings
from prova.errors import StructuredError
from prova.errors import StructuredErrorKind as Kind
from prova.llm.client import LLMClient
from prova.llm.stop import describe_refusal
from prova.llm.usage import UsageLog, UsageRecord
from prova.validation import describe_errors

__all__ = [
    "BugClassification",
    "StructuredError",
    "check_stop_reason",
    "extract",
    "parse_structured",
]


class BugClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "major", "minor"]
    category: Literal["logic", "security", "resource_leak", "error_handling", "other"]
    confidence: float = Field(ge=0, le=1)
    reason: str


def extract[T: BaseModel](  # noqa: PLR0913 - the request, and where its cost goes
    llm: LLMClient,
    schema: type[T],
    *,
    system: str,
    user: str,
    settings: Settings,
    usage: UsageLog | None = None,
) -> T:
    """Get one `schema` instance from the model. Raises StructuredError for anything it cannot fully trust.

    The API is asked to follow the JSON schema, but that is a request, not a guarantee: constraints such as
    ge/le are only described in text, and a reply can be cut or refused. So the reply is checked and
    validated again. With `usage`, what the call cost is added to it first, even when the reply is then
    refused: a call that failed was still paid for.
    """
    response = llm.messages.create(
        model=settings.model,
        max_tokens=settings.max_tokens,
        messages=[{"role": "user", "content": user}],
        system=system,
        output_config={"format": {"type": "json_schema", "schema": transform_schema(schema)}},
    )
    if usage is not None:
        used = response.usage
        usage.add(
            UsageRecord(
                turn=len(usage.records) + 1,
                input_tokens=used.input_tokens,
                output_tokens=used.output_tokens,
                cache_creation_input_tokens=used.cache_creation_input_tokens or 0,
                cache_read_input_tokens=used.cache_read_input_tokens or 0,
            )
        )
    check_stop_reason(response)
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text:
        raise StructuredError(Kind.NO_TEXT, "response has no text block")
    return parse_structured(schema, text)


def check_stop_reason(response: Message) -> None:
    """Only `end_turn` is a complete answer. Every other value, known or not, is an error."""
    reason = response.stop_reason
    if reason == "end_turn":
        return
    if reason == "max_tokens":
        raise StructuredError(Kind.TRUNCATED, "response was truncated (max_tokens): the output is incomplete")
    if reason == "refusal":
        raise StructuredError(Kind.REFUSED, describe_refusal(response))
    raise StructuredError(Kind.UNEXPECTED_STOP, f"unexpected stop_reason {reason!r}")


def parse_structured[T: BaseModel](schema: type[T], text: str) -> T:
    """Validate `text` (a JSON document) against `schema`. Raises StructuredError with a `kind`."""
    try:
        return schema.model_validate_json(text)
    except ValidationError as e:
        raise StructuredError(_kind_of(e), _describe(schema, e)) from e


# When one reply has several problems, report the most basic one so each reply is counted exactly once.
_KIND_PRIORITY = (
    Kind.INVALID_JSON,
    Kind.MISSING_FIELD,
    Kind.OUT_OF_ENUM,
    Kind.OUT_OF_RANGE,
    Kind.EXTRA_FIELD,
    Kind.WRONG_TYPE,
)
_RANGE_ERRORS = {"greater_than", "greater_than_equal", "less_than", "less_than_equal"}


def _kind_of_error_type(error_type: str) -> Kind:
    if error_type == "json_invalid":
        return Kind.INVALID_JSON
    if error_type == "missing":
        return Kind.MISSING_FIELD
    if error_type in ("literal_error", "enum"):
        return Kind.OUT_OF_ENUM
    if error_type in _RANGE_ERRORS:
        return Kind.OUT_OF_RANGE
    if error_type == "extra_forbidden":
        return Kind.EXTRA_FIELD
    if error_type.endswith(("_type", "_parsing")):
        return Kind.WRONG_TYPE
    return Kind.OTHER


def _kind_of(error: ValidationError) -> Kind:
    found = {_kind_of_error_type(e["type"]) for e in error.errors()}
    return next((kind for kind in _KIND_PRIORITY if kind in found), Kind.OTHER)


def _describe(schema: type[BaseModel], error: ValidationError) -> str:
    return f"output does not match {schema.__name__}: " + describe_errors(error)
