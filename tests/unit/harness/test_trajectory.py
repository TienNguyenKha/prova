"""Tests for prova.harness.trajectory: an append-only event log of a run, one JSON object per line."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from prova.errors import TrajectoryError
from prova.harness.redact import Redactor
from prova.harness.trajectory import (
    ModelRequestEvent,
    ModelResponseEvent,
    OutcomeEvent,
    TaskEvent,
    ToolCallEvent,
    ToolResultEvent,
    TrajectoryEvent,
    TrajectoryWriter,
)

ANTHROPIC_LIKE = "sk-ant-api03-AbC123_xyz-9876-QwErTy"  # the shape of a key, not a real one


class StepClock:
    """Starts at a fixed instant and moves one second every time it is read."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(seconds=1)
        return current


def writer_at(path: Path, *, run_id: str = "run-1", redactor: Redactor | None = None) -> TrajectoryWriter:
    return TrajectoryWriter(path, run_id=run_id, clock=StepClock(), redactor=redactor or Redactor())


def lines_of(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- The record: envelope added by the writer, payload from the event ----------


def test_a_record_carries_the_envelope_and_the_payload(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    writer_at(path).write(TaskEvent(task="find the bug"))

    (record,) = lines_of(path)
    assert record == {
        "schema_version": 1,
        "ts": "2026-11-22T10:00:00+00:00",
        "run_id": "run-1",
        "seq": 0,
        "type": "task",
        "task": "find the bug",
    }


def test_seq_counts_up_from_zero_and_each_event_reads_the_clock_once(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    writer = writer_at(path)
    for turn in range(3):
        writer.write(ModelRequestEvent(turn=turn, model="m", messages=1, tools=["read_file"]))

    records = lines_of(path)
    assert [r["seq"] for r in records] == [0, 1, 2]
    assert [r["ts"] for r in records] == [
        "2026-11-22T10:00:00+00:00",
        "2026-11-22T10:00:01+00:00",
        "2026-11-22T10:00:02+00:00",
    ]


def test_the_file_is_one_json_object_per_line_and_ends_with_a_newline(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    writer = writer_at(path)
    writer.write(TaskEvent(task="a"))
    writer.write(TaskEvent(task="b"))

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert len(text.splitlines()) == 2
    assert all(isinstance(json.loads(line), dict) for line in text.splitlines())


def test_text_that_is_not_ascii_stays_readable(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    writer_at(path).write(TaskEvent(task="Xin chào, tìm lỗi giúp tôi"))
    assert "Xin chào, tìm lỗi giúp tôi" in path.read_text(encoding="utf-8")  # not "à" escapes


# --- Durable: nothing is lost if the process dies ----------------------------


def test_every_event_is_in_the_file_as_soon_as_it_is_written(tmp_path: Path) -> None:
    # No close, no flush call: a crash right after `write` returns must not lose the event.
    path = tmp_path / "run.jsonl"
    writer = writer_at(path)
    writer.write(TaskEvent(task="a"))
    assert len(lines_of(path)) == 1
    writer.write(TaskEvent(task="b"))
    assert len(lines_of(path)) == 2


def test_a_new_run_appends_to_the_file_and_never_overwrites_it(tmp_path: Path) -> None:
    path = tmp_path / "all-runs.jsonl"
    writer_at(path, run_id="run-1").write(TaskEvent(task="first"))
    writer_at(path, run_id="run-2").write(TaskEvent(task="second"))

    records = lines_of(path)
    seen = [(r["run_id"], r["seq"], r["task"]) for r in records]
    assert seen == [("run-1", 0, "first"), ("run-2", 0, "second")]


def test_missing_directories_are_created(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "2026-11-22" / "run.jsonl"
    writer_at(path).write(TaskEvent(task="a"))
    assert path.exists()


# --- Secrets never reach the file --------------------------------------------


def test_secrets_are_redacted_in_every_field_before_anything_is_written(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    writer = writer_at(path)
    arguments = {"pattern": ANTHROPIC_LIKE}
    call = ToolCallEvent(tool="search_code", tool_use_id="t1", input=arguments, decision="allow")
    writer.write(call)
    writer.write(
        ToolResultEvent(
            tool="search_code",
            tool_use_id="t1",
            success=True,
            error_code=None,
            message=f"found key {ANTHROPIC_LIKE}",
            chars=30,
        )
    )

    text = path.read_text(encoding="utf-8")
    assert ANTHROPIC_LIKE not in text
    assert text.count("[REDACTED]") == 2


def test_values_of_sensitive_environment_variables_are_redacted_too(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    redactor = Redactor.from_environ({"SOME_SERVICE_KEY": "a-value-without-any-shape"})
    writer_at(path, redactor=redactor).write(TaskEvent(task="use a-value-without-any-shape please"))
    assert lines_of(path)[0]["task"] == "use [REDACTED] please"


# --- It refuses to log badly -------------------------------------------------


def test_a_clock_without_a_time_zone_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    writer = TrajectoryWriter(path, run_id="r", clock=lambda: datetime(2026, 1, 1), redactor=Redactor())  # noqa: DTZ001
    with pytest.raises(ValueError, match="time zone"):
        writer.write(TaskEvent(task="a"))
    assert not path.exists()  # a timestamp that cannot be compared with others is worse than none


def test_a_log_that_cannot_be_written_stops_the_run_with_an_error_naming_the_file(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where a directory should be", encoding="utf-8")
    path = blocker / "run.jsonl"

    with pytest.raises(TrajectoryError, match="not-a-directory"):
        writer_at(path).write(TaskEvent(task="a"))


# --- The events ---------------------------------------------------------------

EVENTS: list[tuple[TrajectoryEvent, str]] = [
    (TaskEvent(task="t"), "task"),
    (ModelRequestEvent(turn=1, model="m", messages=1, tools=["a", "b"]), "model_request"),
    (
        ModelResponseEvent(
            turn=1, stop_reason="end_turn", input_tokens=10, output_tokens=5, text="hi", tool_calls=0
        ),
        "model_response",
    ),
    (ToolCallEvent(tool="a", tool_use_id="t1", input={"path": "x"}, decision="allow"), "tool_call"),
    (
        ToolResultEvent(
            tool="a", tool_use_id="t1", success=False, error_code="not_found", message="m", chars=1
        ),
        "tool_result",
    ),
    (OutcomeEvent(status="done", iterations=2, detail="", input_tokens=20, output_tokens=7), "outcome"),
]


@pytest.mark.parametrize(("event", "expected_type"), EVENTS, ids=[e[1] for e in EVENTS])
def test_each_kind_of_event_is_written_with_its_own_type(
    tmp_path: Path, event: TrajectoryEvent, expected_type: str
) -> None:
    path = tmp_path / "run.jsonl"
    writer_at(path).write(event)
    assert lines_of(path)[0]["type"] == expected_type


def test_a_tool_call_decision_is_allow_or_deny_and_nothing_else() -> None:
    with pytest.raises(ValidationError, match="decision"):
        ToolCallEvent(tool="a", tool_use_id="t", input={}, decision="maybe")  # pyright: ignore[reportArgumentType]


def test_an_event_cannot_carry_fields_it_does_not_declare() -> None:
    with pytest.raises(ValidationError, match="extra"):
        TaskEvent(task="a", extra=1)  # pyright: ignore[reportCallIssue]
