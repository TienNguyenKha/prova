"""The trajectory viewer: what a run did, step by step, in a terminal.

It reads the log and shows it; it measures nothing of its own except times (from the timestamps) and money
(from the tokens and a price table), and it says "n/a" where either cannot be known. What is shown comes from
the model and from tools, so control characters are never passed on to the terminal.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bench.pricing import ModelPrice, PriceTable
from bench.view import ViewError, load_events, render

START = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)
TABLE = PriceTable(
    as_of=date(2026, 11, 3), source="test",
    models={"m": ModelPrice(input_per_mtok=2.0, output_per_mtok=10.0)},
)  # fmt: skip


def ev(kind: str, at: float, **fields: Any) -> dict[str, Any]:
    """An event of the log, `at` seconds after the start of the run."""
    return {
        "schema_version": 1, "ts": (START + timedelta(seconds=at)).isoformat(),
        "run_id": "run1", "seq": 0, "type": kind, **fields,
    }  # fmt: skip


def task(at: float = 0.0, **fields: Any) -> dict[str, Any]:
    return ev("task", at, task="fix the bug\nsecond line", prompt_hash="ab" * 32, **fields)


def turn(n: int, at: float, took: float, *, tokens: tuple[int, int] = (1000, 500), calls: int = 1,
         stop: str = "tool_use", model: str = "m", text: str = "") -> list[dict[str, Any]]:  # fmt: skip
    return [
        ev("model_request", at, turn=n, model=model, messages=1, tools=["read_file"]),
        ev("model_response", at + took, turn=n, stop_reason=stop, input_tokens=tokens[0],
           output_tokens=tokens[1], text=text, tool_calls=calls),
    ]  # fmt: skip


def call(at: float, tool: str = "read_file", args: dict[str, Any] | None = None, decision: str = "allow",
         ) -> dict[str, Any]:  # fmt: skip
    return ev("tool_call", at, tool=tool, tool_use_id="a", input=args or {"path": "x.py"}, decision=decision)


def result(at: float, tool: str = "read_file", *, ok: bool = True, code: str | None = None,
           message: str = "read x.py") -> dict[str, Any]:  # fmt: skip
    return ev(
        "tool_result", at, tool=tool, tool_use_id="a", success=ok, error_code=code, message=message, chars=9
    )


def hook(at: float, name: str = "lint_after_edit", action: str = "allow", message: str = "",
         tool: str | None = "edit_file") -> dict[str, Any]:  # fmt: skip
    return ev("hook_decision", at, hook_kind="post_tool_use", hook=name, tool=tool, action=action,
              message=message, error=None)  # fmt: skip


def outcome(at: float, status: str = "done", iterations: int = 1) -> dict[str, Any]:
    return ev(
        "outcome", at, status=status, iterations=iterations, detail="", input_tokens=1000, output_tokens=500
    )


def show(events: list[dict[str, Any]], table: PriceTable | None = TABLE) -> str:
    return render(events, table)


def a_run() -> list[dict[str, Any]]:
    return [task(), *turn(1, 0.1, 1.9), call(2.1), result(2.2), *turn(2, 2.3, 0.7, calls=0, stop="end_turn"),
            outcome(3.0, iterations=2)]  # fmt: skip


# --- the header and the turns -----------------------------------------------------------------------


def test_the_header_names_the_run_the_task_and_the_start_of_the_prompt_hash() -> None:
    out = show(a_run())
    assert "run1" in out and "fix the bug" in out and "abababab" in out
    assert "second line" not in out  # the first line of the task only: it can be long


def test_each_turn_shows_how_long_the_model_took_the_tokens_the_cost_and_why_it_stopped() -> None:
    line = next(line for line in show(a_run()).splitlines() if "turn 1" in line)
    assert "1.9s" in line and "1000" in line and "500" in line and "$0.0070" in line and "tool_use" in line


def test_the_cost_is_n_a_without_a_price_and_for_a_model_the_table_does_not_know() -> None:
    assert "n/a" in next(line for line in show(a_run(), None).splitlines() if "turn 1" in line)
    unknown = [task(), *turn(1, 0.1, 1.0, model="other"), outcome(2.0)]
    assert "n/a" in next(line for line in show(unknown).splitlines() if "turn 1" in line)


def test_the_task_line_skips_the_tag_that_wraps_the_issue() -> None:
    wrapped = ev("task", 0.0, task="<untrusted_issue>\nthe bug is here\n</untrusted_issue>", prompt_hash=None)
    out = show([wrapped, outcome(1.0)])
    assert "task: the bug is here" in out


def test_a_prompt_larger_than_the_price_holds_for_is_n_a_and_not_a_wrong_cost() -> None:
    small = PriceTable(
        as_of=date(2026, 11, 3), source="test",
        models={"m": ModelPrice(input_per_mtok=2.0, output_per_mtok=10.0, max_prompt_tokens=100)},
    )  # fmt: skip
    line = next(line for line in show(a_run(), small).splitlines() if "turn 1" in line)  # 1000 tokens in
    assert "n/a" in line


# --- the tools and the hooks ------------------------------------------------------------------------


def test_a_tool_call_shows_the_tool_its_arguments_how_it_ended_and_how_long_it_took() -> None:
    line = next(line for line in show(a_run()).splitlines() if "read_file" in line and "x.py" in line)
    assert '"path": "x.py"' in line and "ok" in line and "0.1s" in line and "read x.py" in line


def test_a_tool_that_failed_shows_its_error_code() -> None:
    failed = result(1.3, ok=False, code="not_found", message="no such file")
    events = [task(), *turn(1, 0.1, 1.0), call(1.2), failed, outcome(2.0)]
    assert "not_found" in show(events)


def test_a_call_that_was_denied_says_so_and_has_no_result_to_show() -> None:
    events = [task(), *turn(1, 0.1, 1.0), call(1.2, "edit_file", decision="deny"), outcome(2.0)]
    assert "denied" in next(line for line in show(events).splitlines() if "edit_file" in line)


def test_a_hook_that_said_no_or_not_yet_is_marked_and_one_that_allowed_is_not() -> None:
    events = [
        task(), *turn(1, 0.1, 1.0), call(1.2, "edit_file"),
        hook(1.3, "lint_after_edit", "allow"),
        hook(1.4, "tests_before_stop", "continue", "the tests are red", tool=None),
        hook(1.5, "files_listed", "deny", "not listed"),
        result(1.6, "edit_file"), outcome(2.0),
    ]  # fmt: skip
    lines = show(events).splitlines()
    assert "!" not in next(line for line in lines if "lint_after_edit" in line)
    assert "!" in next(line for line in lines if "tests_before_stop" in line)
    assert "the tests are red" in show(events)
    assert "!" in next(line for line in lines if "files_listed" in line)


def test_long_arguments_are_cut_and_the_cut_is_shown() -> None:
    events = [task(), *turn(1, 0.1, 1.0), call(1.2, args={"path": "x" * 500}), result(1.3), outcome(2.0)]
    line = next(line for line in show(events).splitlines() if "xxxx" in line)
    assert len(line) < 250 and "…" in line


# --- the end ----------------------------------------------------------------------------------------


def test_the_summary_has_the_outcome_the_turns_the_tokens_the_cost_and_the_time() -> None:
    summary = show(a_run()).splitlines()[-1]
    assert "done" in summary and "2 turn" in summary and "$0.0140" in summary and "3.0s" in summary
    assert "2000" in summary and "1000" in summary  # two turns of 1000 in and 500 out


def test_the_total_cost_is_n_a_when_any_turn_is_n_a() -> None:
    events = [task(), *turn(1, 0.1, 1.0), *turn(2, 1.2, 1.0, model="other"), outcome(3.0, iterations=2)]
    assert "n/a" in show(events).splitlines()[-1]


def test_a_run_with_no_outcome_is_shown_as_one_that_did_not_finish() -> None:
    events = [task(), *turn(1, 0.1, 1.0), call(1.2)]
    assert "did not finish" in show(events).splitlines()[-1]


def test_an_event_the_viewer_does_not_know_is_shown_and_not_dropped() -> None:
    assert "mystery" in show([task(), ev("mystery", 1.0), outcome(2.0)])


# --- what the terminal must not be given ------------------------------------------------------------


def test_control_characters_from_the_model_and_from_tools_are_not_passed_on() -> None:
    nasty = "hi\x1b[31m red \x07 and\rmore"
    events = [task(), *turn(1, 0.1, 1.0, text=nasty), call(1.2, args={"path": nasty}),
              result(1.3, message=nasty), outcome(2.0)]  # fmt: skip
    out = show(events)
    assert "\x1b" not in out and "\x07" not in out and "\r" not in out


# --- reading the file -------------------------------------------------------------------------------


def test_a_trajectory_file_is_read_line_by_line(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in a_run()) + "\n\n", encoding="utf-8")
    assert [e["type"] for e in load_events(path)][0] == "task" and len(load_events(path)) == len(a_run())


def test_a_line_that_is_not_json_names_the_line_and_an_empty_file_is_an_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(task()) + "\nnot json\n", encoding="utf-8")
    with pytest.raises(ViewError, match="line 2"):
        load_events(bad)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ViewError, match="no event"):
        load_events(empty)
    with pytest.raises(ViewError, match="cannot read"):
        load_events(tmp_path / "missing.jsonl")
