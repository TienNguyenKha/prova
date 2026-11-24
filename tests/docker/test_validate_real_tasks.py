"""The validator on the real tasks, with real pytest in a real container.

The unit tests of the validator use a fake that plays pytest. These are the proof that the whole chain holds:
red on the original code, green with the reference patch, the same every time.
  uv run pytest -m docker -q
"""

import difflib
import shutil
from pathlib import Path

import pytest

from bench.validate_tasks import validate
from prova.sandbox.docker import DockerSandbox

pytestmark = pytest.mark.docker

TASKS = Path(__file__).resolve().parents[2] / "bench" / "tasks"
FOLDERS = sorted(path for path in TASKS.glob("*/*") if path.is_dir())


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda path: path.name)
def test_every_task_of_the_benchmark_passes_the_cross_check(folder: Path) -> None:
    report = validate(folder, DockerSandbox)
    assert report.problems == ()


def test_a_reference_patch_that_fixes_nothing_is_caught_by_the_real_pytest(tmp_path: Path) -> None:
    folder = tmp_path / "t001_injection_regex"
    shutil.copytree(TASKS / "dev" / "t001_injection_regex", folder)
    old = "A small guard that flags text trying to override an assistant's instructions."
    new = "A small guard that flags text which tries to override an assistant's instructions."
    # It applies and changes only the docstring: the bug stays.
    header = "--- a/injection_guard.py\n+++ b/injection_guard.py\n@@ -1,3 +1,3 @@\n"
    useless = f'{header}-"""{old}"""\n+"""{new}"""\n \n import re\n'
    (folder / "reference.patch").write_text(useless, encoding="utf-8")
    report = validate(folder, DockerSandbox)
    assert any("does not pass with the reference patch" in p for p in report.problems), report.problems


def test_a_patch_line_that_no_hidden_test_runs_is_caught_by_the_real_coverage(tmp_path: Path) -> None:
    folder = tmp_path / "t001_injection_regex"
    shutil.copytree(TASKS / "dev" / "t001_injection_regex", folder)
    before = (folder / "repo" / "injection_guard.py").read_text(encoding="utf-8")
    old_regex = 'r"ignore (all |your |previous )?instructions"'
    fixed = before.replace(old_regex, r'r"ignore\s+(?:(?:all|your|previous)\s+)*instructions"')
    assert fixed != before
    after = fixed + "\n\ndef never_called() -> int:\n    return 1\n"  # added by the patch, run by no test
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        "a/injection_guard.py",
        "b/injection_guard.py",
    )
    (folder / "reference.patch").write_text("".join(diff), encoding="utf-8")
    report = validate(folder, DockerSandbox)
    assert any("never run" in p for p in report.problems), report.problems
    assert not any("does not apply" in p for p in report.problems), report.problems
