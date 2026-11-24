"""The tasks in bench/tasks must all load: a task that is wrong is found here, not in the middle of a run."""

from pathlib import Path

import pytest

from bench.task import load_task

TASKS = Path(__file__).resolve().parents[3] / "bench" / "tasks"
FOLDERS = sorted(path for path in TASKS.glob("*/*") if path.is_dir())


def test_there_is_at_least_one_task() -> None:
    assert FOLDERS


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda path: path.name)
def test_a_task_loads_and_sits_in_the_folder_of_its_split(folder: Path) -> None:
    task = load_task(folder)
    assert task.split == folder.parent.name
