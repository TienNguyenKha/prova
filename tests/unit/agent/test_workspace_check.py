"""What the fix loop reads and runs: the text files of a workspace, and a check run in the sandbox.

The files are read to tell the agent what an attempt really changed, so they are read as the agent left
them, without following a link out of the workspace: a link to a file of the host would put it in the prompt.
The check runs in the sandbox, never on the host, and its result is handed on as it is: a check that ran out
of time stays one, whatever its exit code says.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from prova.agent.workspace_check import read_text_files, run_check
from prova.errors import SandboxUnavailableError
from prova.sandbox.base import ExecResult


class Sandbox:
    """Stands in for the sandbox: gives back one result and remembers how it was called."""

    def __init__(self, result: ExecResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[list[str], float, str]] = []

    def run(
        self,
        argv: list[str],
        *,
        timeout_s: float,
        cwd: str = "/work",
        on_output: Callable[[str], None] | None = None,
    ) -> ExecResult:
        self.calls.append((argv, timeout_s, cwd))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self) -> None:
        pass


def result(exit_code: int, stdout: str = "", stderr: str = "", *, timed_out: bool = False) -> ExecResult:
    return ExecResult(exit_code, stdout, stderr, timed_out=timed_out, duration_s=0.1, truncated=False)


# --- the files -----------------------------------------------------------------------------------------


def test_the_text_files_are_read_by_their_path_inside_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("y = 2\n", encoding="utf-8")
    assert read_text_files(tmp_path) == {"a.py": "x = 1\n", "pkg/b.py": "y = 2\n"}


def test_an_empty_workspace_has_no_files(tmp_path: Path) -> None:
    assert read_text_files(tmp_path) == {}


@pytest.mark.parametrize("folder", ["__pycache__", ".pytest_cache", ".git", "pkg/__pycache__"])
def test_what_tools_leave_behind_is_not_read(tmp_path: Path, folder: str) -> None:
    (tmp_path / folder).mkdir(parents=True)
    (tmp_path / folder / "left.txt").write_text("noise", encoding="utf-8")
    (tmp_path / "kept.py").write_text("x\n", encoding="utf-8")
    assert read_text_files(tmp_path) == {"kept.py": "x\n"}


def test_a_file_that_is_not_text_is_left_out(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x81")
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    assert read_text_files(tmp_path) == {"a.py": "x\n"}


def test_a_link_is_not_followed_out_of_the_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOKEN=abc", encoding="utf-8")
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "secret.txt").symlink_to(outside / "secret.txt")
    (workspace / "linked_dir").symlink_to(outside)
    assert read_text_files(workspace) == {}


# --- the check -----------------------------------------------------------------------------------------


def test_the_check_is_run_in_the_sandbox_in_the_workspace_with_its_time_limit() -> None:
    sandbox = Sandbox(result(0))
    run_check(sandbox, ["python", "-m", "pytest", "-q"], timeout_s=120)
    assert sandbox.calls == [(["python", "-m", "pytest", "-q"], 120, "/work")]


def test_the_exit_code_and_all_the_output_are_handed_on() -> None:
    check = run_check(Sandbox(result(1, "1 failed\n", "warning\n")), ["pytest"], timeout_s=60)
    assert check.exit_code == 1 and check.output == "1 failed\nwarning\n" and check.timed_out is False


def test_a_check_that_ran_out_of_time_says_so_whatever_its_exit_code() -> None:
    check = run_check(Sandbox(result(0, timed_out=True)), ["pytest"], timeout_s=60)
    assert check.timed_out is True


def test_a_sandbox_that_cannot_run_is_an_error_and_never_a_result() -> None:
    with pytest.raises(SandboxUnavailableError):
        run_check(Sandbox(SandboxUnavailableError("no docker")), ["pytest"], timeout_s=60)
