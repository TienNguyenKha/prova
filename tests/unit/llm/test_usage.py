"""Tests for prova.llm.usage: per-turn usage records, running totals and CSV export."""

import csv
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from prova.llm.usage import UsageLog, UsageRecord


def record(
    turn: int,
    input_tokens: int = 10,
    output_tokens: int = 5,
    latency_s: float = 0.0,
    ttft_s: float | None = None,
) -> UsageRecord:
    return UsageRecord(
        turn=turn,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_s=latency_s,
        ttft_s=ttft_s,
    )


# --- Success paths ----------------------------------------------------------


def test_empty_log_has_no_records_and_zero_totals() -> None:
    log = UsageLog()
    assert log.records == ()
    assert log.total_input_tokens == 0
    assert log.total_output_tokens == 0


def test_totals_sum_over_all_turns() -> None:
    log = UsageLog()
    log.add(record(1, input_tokens=20, output_tokens=5))
    log.add(record(2, input_tokens=40, output_tokens=7))
    assert log.total_input_tokens == 60
    assert log.total_output_tokens == 12


def test_records_keep_insertion_order_and_cannot_be_mutated() -> None:
    log = UsageLog()
    log.add(record(1))
    log.add(record(2))
    assert [r.turn for r in log.records] == [1, 2]
    assert isinstance(log.records, tuple)  # a copy: callers cannot change the log through it


def test_usage_record_defaults() -> None:
    rec = record(1)
    assert rec.cache_creation_input_tokens == 0
    assert rec.cache_read_input_tokens == 0
    assert rec.latency_s == 0.0
    assert rec.ttft_s is None


def test_usage_record_is_frozen() -> None:
    rec = record(1)
    with pytest.raises(FrozenInstanceError):
        rec.turn = 2  # pyright: ignore[reportAttributeAccessIssue]


# --- CSV --------------------------------------------------------------------


def test_to_csv_writes_header_and_one_row_per_record(tmp_path: Path) -> None:
    log = UsageLog()
    log.add(record(1, input_tokens=20, output_tokens=5, latency_s=1.5))
    log.add(record(2, input_tokens=40, output_tokens=7, latency_s=2.0, ttft_s=0.25))
    path = tmp_path / "usage.csv"

    log.to_csv(path)

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert reader.fieldnames == [
            "turn",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "latency_s",
            "ttft_s",
        ]
    assert [r["turn"] for r in rows] == ["1", "2"]
    assert rows[0]["input_tokens"] == "20"
    assert rows[0]["latency_s"] == "1.5"
    assert rows[0]["ttft_s"] == ""  # None becomes an empty cell
    assert rows[1]["ttft_s"] == "0.25"


def test_to_csv_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "m1" / "usage.csv"
    UsageLog().to_csv(path)
    assert path.exists()


# --- Error paths ------------------------------------------------------------


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_negative_token_counts_are_rejected(field: str) -> None:
    kwargs = {"turn": 1, "input_tokens": 1, "output_tokens": 1, field: -1}
    with pytest.raises(ValueError, match=field):
        UsageRecord(**kwargs)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("bad_turn", [1, 0])
def test_turns_must_strictly_increase(bad_turn: int) -> None:
    log = UsageLog()
    log.add(record(1))
    with pytest.raises(ValueError, match="turn"):
        log.add(record(bad_turn))
    assert len(log.records) == 1  # the failed add left the log unchanged
