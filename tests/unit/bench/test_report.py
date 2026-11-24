"""The report: a table of the results, where what is not known is shown as not known.

A mean over numbers that are partly missing would look like a result and be a guess, so a column with a
missing value in it says "n/a". A run that crashed or broke a rule is in the table, not left out of it.
"""

from pathlib import Path
from typing import Any

import pytest

from bench.report import ReportError, load_results, render


def row(task: str = "t001", seed: int = 0, **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run1", "task_id": task, "seed": seed, "config": {"model": "m"},
        "resolved": True, "violation": False, "error": None, "agent_status": "done",
        "localization_file": True, "patch_size_ratio": 1.0, "tool_calls": 10, "tool_errors": 0,
        "cost_usd": 0.10, "seconds": 30.0,
    }  # fmt: skip
    return {**base, **changes}


def lines_of(text: str, containing: str) -> list[str]:
    return [line for line in text.splitlines() if containing in line]


# --- the table ------------------------------------------------------------------------------------


def test_each_task_is_a_line_sorted_by_id_with_resolved_as_k_of_n() -> None:
    text = render(
        [row("t002", resolved=False), row("t001", 0), row("t001", 1, resolved=False), row("t001", 2)]
    )
    assert text.index("t001") < text.index("t002")
    assert "2/3" in lines_of(text, "t001")[0]
    assert "0/1" in lines_of(text, "t002")[0]


def test_the_numbers_are_means_over_the_seeds_of_the_task() -> None:
    text = render(
        [
            row(tool_calls=10, cost_usd=0.10, seconds=30.0),
            row(seed=1, tool_calls=20, cost_usd=0.30, seconds=50.0),
        ]
    )
    line = lines_of(text, "t001")[0]
    assert "15.0" in line and "0.2000" in line and "40.0" in line


def test_a_column_with_a_value_not_known_is_n_a_and_not_a_mean_of_the_rest() -> None:
    text = render([row(cost_usd=0.10), row(seed=1, cost_usd=None)])
    line = lines_of(text, "t001")[0]
    assert "n/a" in line and "0.1000" not in line


def test_runs_that_broke_a_rule_or_ended_in_error_are_counted_and_not_hidden() -> None:
    text = render(
        [row(violation=True, resolved=False), row(seed=1, error="the agent crashed: X", resolved=False)]
    )
    line = lines_of(text, "t001")[0]
    assert "0/2" in line and "violations 1" in line and "errors 1" in line


def test_the_total_cost_is_n_a_when_any_cost_is_not_known() -> None:
    known = render([row("t001", cost_usd=0.10), row("t002", cost_usd=0.20)])
    unknown = render([row("t001", cost_usd=0.10), row("t002", cost_usd=None)])
    assert "total cost: $0.3000" in known
    assert "total cost: n/a" in unknown


def test_the_report_says_that_one_seed_is_not_enough_to_compare() -> None:
    assert "1 seed" in render([row()])
    assert "seeds" in render([row(seed=0), row(seed=1), row(seed=2)])


def test_the_note_on_seeds_goes_by_the_task_with_the_fewest_and_two_are_still_not_enough() -> None:
    few = render([row("t001", 0), row("t001", 1), row("t001", 2), row("t002", 0)])
    assert "1 seed per task" in few
    assert "needs >= 3" in render([row(seed=0), row(seed=1)])
    assert "needs" not in render([row(seed=0), row(seed=1), row(seed=2)])


def test_a_value_that_is_not_a_number_or_not_a_real_yes_is_not_counted_or_summed() -> None:
    assert "0/1" in lines_of(render([row(resolved=1)]), "t001")[0]  # 1 is not True
    assert "n/a" in lines_of(render([row(cost_usd=True)]), "t001")[0]  # True is not one dollar


# --- reading the file -----------------------------------------------------------------------------


def test_a_results_file_is_read_line_by_line(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text('{"task_id": "a"}\n\n{"task_id": "b"}\n', encoding="utf-8")
    assert [r["task_id"] for r in load_results(path)] == ["a", "b"]


def test_a_line_that_is_not_json_is_an_error_that_names_the_line(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text('{"task_id": "a"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ReportError, match="line 2"):
        load_results(path)


def test_a_file_with_no_result_is_an_error_and_not_an_empty_table(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ReportError, match="no result"):
        load_results(path)


def test_a_file_that_is_not_there_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="cannot read"):
        load_results(tmp_path / "missing.jsonl")
