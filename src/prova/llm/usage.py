"""Per-turn token usage: records, running totals and CSV export."""

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Usage of one turn, as reported by the API plus our own timings."""

    turn: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    latency_s: float = 0.0
    ttft_s: float | None = None  # time to first token; None when not streaming

    def __post_init__(self) -> None:
        for name in _TOKEN_FIELDS:
            value: int = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")


class UsageLog:
    """Ordered log of UsageRecord, one per turn."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    @property
    def records(self) -> tuple[UsageRecord, ...]:
        return tuple(self._records)

    @property
    def total_input_tokens(self) -> int:
        """Sum of `input_tokens` as reported by the API (cache tokens are tracked in separate fields)."""
        return sum(r.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self._records)

    def add(self, rec: UsageRecord) -> None:
        if self._records and rec.turn <= self._records[-1].turn:
            raise ValueError(f"turn must increase: got {rec.turn} after {self._records[-1].turn}")
        self._records.append(rec)

    def to_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[field.name for field in fields(UsageRecord)])
            writer.writeheader()
            writer.writerows(asdict(rec) for rec in self._records)
