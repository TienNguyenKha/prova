"""The grader on the real tasks, with real pytest in a real container.

For each task: a workspace with the reference patch applied is resolved, and one left as it was is not.
  uv run pytest -m docker -q
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from bench.grade import Grade, grade
from bench.task import load_task
from bench.violations import find_violations
from bench.workspace import collect_changes, prepare_workspace
from prova.sandbox.docker import DockerSandbox

pytestmark = pytest.mark.docker

TASKS = Path(__file__).resolve().parents[2] / "bench" / "tasks"
FOLDERS = sorted(path for path in TASKS.glob("*/*") if path.is_dir())


def grade_workspace(folder: Path, workspace: Path) -> Grade:
    task = load_task(folder)
    changes = collect_changes(folder / "repo", workspace)
    return grade(
        task, folder, workspace,
        changes=changes, violations=find_violations(task, changes, None),
        trajectory=None, open_sandbox=DockerSandbox,
    )  # fmt: skip


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda path: path.name)
def test_the_reference_fix_is_resolved_and_the_original_code_is_not(folder: Path, tmp_path: Path) -> None:
    fixed = tmp_path / "fixed"
    prepare_workspace(folder, fixed)
    git = shutil.which("git")
    assert git is not None
    done = subprocess.run(  # noqa: S603 - a patch of the repo itself, applied in a temporary folder
        [git, "-C", str(fixed), "apply", str(folder / "reference.patch")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr

    good = grade_workspace(folder, fixed)
    assert good.resolved is True, (good.outcomes, good.error, good.violations)
    assert good.localization_file is True and good.localization_symbol is True
    assert good.patch_size_ratio == pytest.approx(1.0)  # the same patch: the same size

    untouched = tmp_path / "untouched"
    prepare_workspace(folder, untouched)
    bad = grade_workspace(folder, untouched)
    assert bad.resolved is False and bad.error is None
    assert "fail" in bad.outcomes.values() and bad.patch_size_ratio == 0.0
