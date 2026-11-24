"""A table of benchmark results, where what is not known is shown as not known.

A mean over numbers that are partly missing would look like a result and be a guess, so a column with a
missing value in it says "n/a". A run that crashed or broke a rule is in the table, not left out of it.

Read a results file:  uv run python -m bench.report bench/results/<run_id>.jsonl
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

Row = dict[str, Any]
NOT_KNOWN = "n/a"


class ReportError(Exception):
    """The results cannot be read, so there is nothing true to show."""


def load_results(path: Path) -> list[Row]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ReportError(f"cannot read {path}: {e.strerror or e}") from e
    rows: list[Row] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            raise ReportError(f"line {number} of {path} is not JSON: {e}") from e
        if not isinstance(row, dict):
            raise ReportError(f"line {number} of {path} is not a result (a JSON object)")
        rows.append(cast("Row", row))
    if not rows:
        raise ReportError(f"{path} has no result")
    return rows


def render(rows: list[Row]) -> str:
    by_task: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(row)

    header = ["task", "resolved", "loc@file", "patch ratio", "tool calls", "$", "seconds", "notes"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for task, runs in sorted(by_task.items()):
        violations = sum(1 for r in runs if r.get("violation"))
        errors = sum(1 for r in runs if r.get("error"))
        cells = [
            task,
            _count(runs, "resolved"),
            _count(runs, "localization_file"),
            _mean(runs, "patch_size_ratio", 2),
            _mean(runs, "tool_calls", 1),
            _mean(runs, "cost_usd", 4),
            _mean(runs, "seconds", 1),
            f"violations {violations}, errors {errors}" if violations or errors else "-",
        ]
        lines.append("| " + " | ".join(cells) + " |")

    seeds = min(len(runs) for runs in by_task.values())
    lines += ["", _seeds_note(seeds), f"total cost: {_total_cost(rows)}"]
    return "\n".join(lines)


def _count(runs: list[Row], key: str) -> str:
    return f"{sum(1 for r in runs if r.get(key) is True)}/{len(runs)}"


def _numbers(rows: list[Row], key: str) -> list[float] | None:
    """The value of `key` in every row, or None if any row does not have one: then nothing can be summed."""
    values: list[float] = []
    for r in rows:
        value = r.get(key)
        if not isinstance(value, int | float) or isinstance(value, bool):
            return None
        values.append(float(value))
    return values


def _mean(runs: list[Row], key: str, digits: int) -> str:
    values = _numbers(runs, key)
    return NOT_KNOWN if values is None else f"{sum(values) / len(values):.{digits}f}"


def _total_cost(rows: list[Row]) -> str:
    costs = _numbers(rows, "cost_usd")
    return NOT_KNOWN if costs is None else f"${sum(costs):.4f}"


def _seeds_note(seeds: int) -> str:
    if seeds < 3:  # noqa: PLR2004 - the rule of the project: a claim of "better" needs at least 3 seeds
        unit = "seed" if seeds == 1 else "seeds"
        return f"{seeds} {unit} per task: enough to see that the pipeline runs, not to compare (needs >= 3)."
    return f"{seeds} seeds per task."


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m bench.report <results.jsonl>")  # noqa: T201
        return 2
    try:
        print(render(load_results(Path(argv[0]))))  # noqa: T201
    except ReportError as e:
        print(f"error: {e}")  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
