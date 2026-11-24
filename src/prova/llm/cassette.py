"""Record and replay what the model said, so the harness can be tested without the model.

A cassette is a JSONL file: one line for each call, with the hash of the request and the model's answer.
`RecordingClient` wraps a real client and writes the lines; `ReplayClient` reads them and gives the same
answer to the same request. A request that was not recorded is a `CassetteMiss`: replay never falls back on
the API, because a test that quietly spends money and gets a different answer is not a replay.

The model's answers can hold secrets, so a line goes through the redactor before it reaches the disk. What
comes back from a replay is therefore what was written, with the same redactions.
"""

import hashlib
import json
import re
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from anthropic import Omit, omit
from anthropic.types import Message, MessageParam, OutputConfigParam, ToolParam
from pydantic import BaseModel, ValidationError

from prova.errors import ProvaError
from prova.harness.redact import Redactor
from prova.llm.client import LLMClient, MessagesAPI

SCHEMA_VERSION = 1

# How long a tool took, as the tools and pytest print it: a decimal number just before `s` ("0.6s",
# "in 12.58s"), and pytest's "(0:01:15)" after a long run. A whole number ("120s") is a setting and a word
# ("0.6 seconds") is not the form they print, so neither is touched.
_CLOCK = re.compile(r"\s*\(\d+:\d\d:\d\d\)")
_DURATION = re.compile(r"\b\d+\.\d+s\b")


class CassetteError(ProvaError):
    """The cassette cannot be read or does not hold what a replay needs."""


class CassetteMiss(CassetteError):
    """A replay was asked for an answer that the cassette does not have. Not an `LLMError` on purpose: the
    loop ends a run on those and reports them as a result, and a miss must stop the test instead."""


def request_hash(
    *,
    model: str,
    max_tokens: int,
    messages: Sequence[MessageParam],
    system: str | Omit | None = None,
    output_config: OutputConfigParam | Omit | None = None,
    tools: Sequence[ToolParam] | Omit | None = None,
) -> str:
    """The sha256 of everything in a request that can change the answer. A part that is left out and a part
    that is None are the same, and a block as an object has the same hash as the same block as a dict."""
    parts: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "system": None if isinstance(system, Omit) else system,
        "output_config": None if isinstance(output_config, Omit) else output_config,
        "tools": None if isinstance(tools, Omit) else tools,
    }
    plain = _plain(parts)
    plain["messages"] = _without_durations(plain["messages"])
    text = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _without_durations(messages: list[Any]) -> list[Any]:
    """The messages with the time that a tool took left out of its results, and nothing else changed. A run
    is never exactly as fast twice, so a hash that held the time would make every replay a miss. Only what a
    tool answered is touched: the user's words and the system prompt count as they are."""
    for message in messages:
        for block in _blocks(_mapping(message).get("content")):
            if _mapping(block).get("type") == "tool_result":
                cast("dict[str, Any]", block)["content"] = _scrub(
                    cast("dict[str, Any]", block).get("content")
                )
    return messages


def _mapping(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _blocks(value: Any) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _scrub(content: Any) -> Any:
    if isinstance(content, str):
        return _DURATION.sub("<duration>", _CLOCK.sub("", content))
    if isinstance(content, list):
        return [_scrub_block(item) for item in cast("list[Any]", content)]
    return content


def _scrub_block(block: Any) -> Any:
    fields = _mapping(block)
    text = fields.get("text")
    return {**fields, "text": _scrub(text)} if isinstance(text, str) else block


def _plain(value: Any) -> Any:
    """JSON-able data with no object types and no None inside a mapping, so equal things hash equal."""
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="json"))
    if isinstance(value, dict):
        pairs = cast("dict[Any, Any]", value)
        return {str(k): _plain(v) for k, v in pairs.items() if v is not None}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in cast("Sequence[Any]", value)]
    return value


# -- recording ------------------------------------------------------------------------------------


class _RecordingMessages:
    def __init__(self, inner: MessagesAPI, path: Path, redactor: Redactor) -> None:
        self._inner, self._path, self._redactor = inner, path, redactor

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
        key = request_hash(
            model=model, max_tokens=max_tokens, messages=messages,
            system=system, output_config=output_config, tools=tools,
        )  # fmt: skip
        response = self._inner.create(
            model=model, max_tokens=max_tokens, messages=messages,
            system=system, output_config=output_config, tools=tools,
        )  # fmt: skip
        entry = {
            "schema_version": SCHEMA_VERSION,
            "request_hash": key,
            "response": response.model_dump(mode="json"),
        }
        line = json.dumps(self._redactor.redact_data(entry), ensure_ascii=False)  # redacted before the disk
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return response


class RecordingClient:
    """Calls the real client and writes down each answer. An error is not an answer, so it is not written."""

    def __init__(self, inner: LLMClient, path: Path, redactor: Redactor) -> None:
        self.messages: MessagesAPI = _RecordingMessages(inner.messages, path, redactor)


# -- replay ---------------------------------------------------------------------------------------


class _ReplayMessages:
    def __init__(self, answers: dict[str, deque[Message]]) -> None:
        self._answers = answers
        self._seen = set(answers)

    @property
    def unused(self) -> int:
        return sum(len(queue) for queue in self._answers.values())

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
        key = request_hash(
            model=model, max_tokens=max_tokens, messages=messages,
            system=system, output_config=output_config, tools=tools,
        )  # fmt: skip
        what = f"model {model!r}, {len(messages)} message(s), hash {key[:12]}"
        queue = self._answers.get(key)
        if queue is None:
            raise CassetteMiss(f"the cassette has no answer for this request ({what})")
        if not queue:
            raise CassetteMiss(f"every answer for this request was already used ({what})")
        return queue.popleft()


class ReplayClient:
    """Answers from a cassette and from nothing else."""

    def __init__(self, path: Path) -> None:
        self._replay = _ReplayMessages(_load(path))
        self.messages: MessagesAPI = self._replay

    @property
    def unused(self) -> int:
        """How many recorded answers have not been asked for: a run that took another way leaves some."""
        return self._replay.unused


def _load(path: Path) -> dict[str, deque[Message]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CassetteError(f"cannot read the cassette {path}: {e.strerror or e}") from e
    answers: dict[str, deque[Message]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        key, message = _entry(path, number, line)
        answers.setdefault(key, deque()).append(message)
    return answers


def _entry(path: Path, number: int, line: str) -> tuple[str, Message]:
    where = f"line {number} of {path}"
    try:
        loaded: object = json.loads(line)
    except ValueError as e:
        raise CassetteError(f"{where} is not JSON: {e}") from e
    if not isinstance(loaded, dict):
        raise CassetteError(f"{where} is not an entry (a JSON object)")
    entry = cast("dict[str, Any]", loaded)
    if entry.get("schema_version") != SCHEMA_VERSION:
        raise CassetteError(
            f"{where} has schema version {entry.get('schema_version')!r}, not {SCHEMA_VERSION}"
        )
    key, response = entry.get("request_hash"), entry.get("response")
    if not isinstance(key, str) or not isinstance(response, dict):
        raise CassetteError(f"{where} needs a request_hash and a response")
    try:
        return key, Message.model_validate(response)
    except ValidationError as e:
        raise CassetteError(f"the response on {where} is not a message: {e.error_count()} problem(s)") from e
