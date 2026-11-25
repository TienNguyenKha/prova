"""The command that runs the benchmark: which tasks, what it may cost, and that nothing is spent by default.

Spending money is a decision, so the command only shows its plan and the most it can cost until it is told
`--confirm`. What it does when confirmed is the work of runner.py and product.py, tested elsewhere.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bench.run import RunError, ceiling_usd, main, new_run_id, select_tasks
from tests.unit.bench.test_task import make_task


def tasks_in(tmp_path: Path, *names: str, split: str = "dev") -> Path:
    """A folder of tasks: root/<split>/<name>/, each with the budget of make_task ($0.50)."""
    root = tmp_path / "tasks"
    (root / split).mkdir(parents=True)
    for name in names:
        made = make_task(tmp_path / "scratch" / name, {"id": name, "split": split})
        made.rename(root / split / name)
    return root


# --- which tasks ------------------------------------------------------------------------------------


def test_every_task_of_the_split_is_selected_in_order_of_name(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t002", "t001")
    assert [f.name for f in select_tasks(root, "dev", [])] == ["t001", "t002"]


def test_only_the_named_tasks_are_selected(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001", "t002")
    assert [f.name for f in select_tasks(root, "dev", ["t002"])] == ["t002"]


def test_a_task_named_twice_runs_once_and_the_order_is_by_name(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001", "t002")
    assert [f.name for f in select_tasks(root, "dev", ["t002", "t001", "t002"])] == ["t001", "t002"]


def test_a_name_that_is_not_a_task_is_an_error_that_says_which(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001")
    with pytest.raises(RunError, match="t999"):
        select_tasks(root, "dev", ["t001", "t999"])


def test_a_split_with_no_task_is_an_error_and_not_an_empty_run(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001")
    with pytest.raises(RunError, match="no task"):
        select_tasks(root, "test", [])


# --- the cost and the id ----------------------------------------------------------------------------


def test_the_ceiling_is_the_money_limit_of_every_task_for_every_seed(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001", "t002")
    assert ceiling_usd(select_tasks(root, "dev", []), seeds=3) == pytest.approx(3.0)  # 2 x 3 x $0.50


def test_a_run_id_has_the_time_and_a_token_and_two_ids_differ() -> None:
    now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
    first, second = new_run_id(now), new_run_id(now)
    assert re.fullmatch(r"2026-12-01T10-00_[0-9a-f]{6}", first) and first != second


# --- the command ------------------------------------------------------------------------------------


def test_without_confirm_it_shows_the_plan_and_the_ceiling_and_spends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tasks_in(tmp_path, "t001", "t002")
    ran: list[object] = []
    code = main(
        ["--seeds", "2"], root=root, execute=lambda folders, seeds, record, loop: ran.append(folders) or 0
    )
    out = capsys.readouterr().out
    assert code == 0 and ran == []
    assert "t001" in out and "t002" in out and "$2.00" in out and "--confirm" in out


def test_with_confirm_it_runs_the_selected_tasks_with_the_seeds(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001", "t002")
    got: list[tuple[list[str], int]] = []

    def execute(folders: list[Path], seeds: int, record: Path | None, loop: bool) -> int:
        got.append(([f.name for f in folders], seeds))
        return 0

    assert main(["--confirm", "--task", "t002", "--seeds", "3"], root=root, execute=execute) == 0
    assert got == [(["t002"], 3)]


def test_a_seed_count_below_one_is_refused(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001")
    with pytest.raises(SystemExit):
        main(["--seeds", "0"], root=root, execute=lambda folders, seeds, record, loop: 0)


def test_an_unknown_task_is_an_error_message_and_a_failing_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tasks_in(tmp_path, "t001")
    assert main(["--task", "t999"], root=root, execute=lambda folders, seeds, record, loop: 0) == 1
    assert "t999" in capsys.readouterr().out


# --- recording a cassette ---------------------------------------------------------------------------


def test_the_plan_says_where_the_cassette_will_be_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tasks_in(tmp_path, "t001")
    cassette = tmp_path / "dev.jsonl"
    main(["--record", str(cassette)], root=root, execute=lambda folders, seeds, record, loop: 0)
    assert str(cassette) in capsys.readouterr().out


def test_with_confirm_the_cassette_path_goes_to_the_run(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001")
    got: list[Path | None] = []

    def execute(folders: list[Path], seeds: int, record: Path | None, loop: bool) -> int:
        got.append(record)
        return 0

    cassette = tmp_path / "dev.jsonl"
    assert main(["--confirm", "--record", str(cassette)], root=root, execute=execute) == 0
    assert got == [cassette]
    main(["--confirm"], root=root, execute=execute)
    assert got[-1] is None  # nothing is recorded unless it is asked for


def test_a_cassette_that_already_exists_is_refused_and_nothing_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tasks_in(tmp_path, "t001")
    cassette = tmp_path / "dev.jsonl"
    cassette.write_text("old\n", encoding="utf-8")
    ran: list[object] = []
    code = main(
        ["--confirm", "--record", str(cassette)], root=root,
        execute=lambda folders, seeds, record, loop: ran.append(1) or 0,
    )  # fmt: skip
    assert code == 1 and ran == [] and "dev.jsonl" in capsys.readouterr().out
    assert cassette.read_text(encoding="utf-8") == "old\n"  # not touched


# --- the loop ---------------------------------------------------------------------------------------


def test_the_plan_says_which_agent_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tasks_in(tmp_path, "t001")
    main([], root=root, execute=lambda folders, seeds, record, loop: 0)
    assert "agent: one run of the product" in capsys.readouterr().out
    main(["--loop"], root=root, execute=lambda folders, seeds, record, loop: 0)
    out = capsys.readouterr().out
    assert "agent: the fix loop, up to 4 attempts that share the budget of the task" in out
    assert "$0.50" in out  # the ceiling does not grow with the attempts


def test_with_confirm_the_choice_of_the_loop_goes_to_the_run(tmp_path: Path) -> None:
    root = tasks_in(tmp_path, "t001")
    got: list[bool] = []

    def execute(folders: list[Path], seeds: int, record: Path | None, loop: bool) -> int:
        got.append(loop)
        return 0

    main(["--confirm", "--loop"], root=root, execute=execute)
    main(["--confirm"], root=root, execute=execute)
    assert got == [True, False]


def test_with_the_loop_the_plan_names_the_tasks_with_no_test_the_agent_can_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tasks_in(tmp_path, "t001", "t002")
    (root / "dev" / "t002" / "repo" / "tests").mkdir()
    (root / "dev" / "t002" / "repo" / "tests" / "test_x.py").write_text("", encoding="utf-8")
    main(["--loop"], root=root, execute=lambda folders, seeds, record, loop: 0)
    out = capsys.readouterr().out
    assert "no test the agent can run, so it ends as unverifiable before any money is spent: t001" in out
    main([], root=root, execute=lambda folders, seeds, record, loop: 0)
    assert "unverifiable" not in capsys.readouterr().out  # one run does not need a visible test
    main(["--loop", "--task", "t002"], root=root, execute=lambda folders, seeds, record, loop: 0)
    assert "unverifiable" not in capsys.readouterr().out  # every task selected has one
