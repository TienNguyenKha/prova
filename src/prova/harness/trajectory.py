"""The trajectory: an append-only log of what a run did, one JSON object per line (read it with `jq`)."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from prova.errors import TrajectoryError
from prova.harness.redact import Redactor

SCHEMA_VERSION = 1


class TrajectoryEvent(BaseModel):
    """Base of every event. The writer adds `schema_version`, `ts`, `run_id` and `seq` around the payload."""

    model_config = ConfigDict(extra="forbid")


class TaskEvent(TrajectoryEvent):
    type: Literal["task"] = "task"
    task: str


class ModelRequestEvent(TrajectoryEvent):
    """A summary of what was sent to the model, not the full text: that would be huge and full of secrets."""

    type: Literal["model_request"] = "model_request"
    turn: int
    model: str
    messages: int
    tools: list[str]


class ModelResponseEvent(TrajectoryEvent):
    type: Literal["model_response"] = "model_response"
    turn: int
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    text: str
    tool_calls: int


class ToolCallEvent(TrajectoryEvent):
    type: Literal["tool_call"] = "tool_call"
    tool: str
    tool_use_id: str
    input: dict[str, Any]
    decision: Literal["allow", "deny"]


class ToolResultEvent(TrajectoryEvent):
    type: Literal["tool_result"] = "tool_result"
    tool: str
    tool_use_id: str
    success: bool
    error_code: str | None
    message: str
    chars: int  # how much the model was shown


class OutcomeEvent(TrajectoryEvent):
    type: Literal["outcome"] = "outcome"
    status: str
    iterations: int
    detail: str
    input_tokens: int
    output_tokens: int


class TrajectoryWriter:
    """Appends events to a JSONL file. The file is opened, written and closed again for each event, so an
    event is in the file when `write` returns: a crash cannot lose it, and no handle can be left open."""

    def __init__(
        self, path: Path, *, run_id: str, clock: Callable[[], datetime], redactor: Redactor
    ) -> None:
        self._path = path
        self._run_id = run_id
        self._clock = clock
        self._redactor = redactor
        self._seq = 0

    def write(self, event: TrajectoryEvent) -> None:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("the clock must return datetimes with a time zone")
        record = {
            "schema_version": SCHEMA_VERSION,
            "ts": now.isoformat(),
            "run_id": self._run_id,
            "seq": self._seq,
            **event.model_dump(mode="json"),
        }
        line = json.dumps(self._redactor.redact_data(record), ensure_ascii=False)  # redacted before the disk
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            raise TrajectoryError(f"cannot write the trajectory file {self._path}: {e.strerror or e}") from e
        self._seq += 1
