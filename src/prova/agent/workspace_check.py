"""What the fix loop reads and runs: the text files of a workspace, and a check run in the sandbox.

The files are read to tell the agent what an attempt really changed. They are read as the agent left them and
no link is followed: a link to a file of the host would put that file in the prompt. The check runs in the
sandbox, never on the host; a sandbox that cannot run raises, and that is never taken for a result.
"""

import os
from pathlib import Path

from prova.agent.fixloop import Verification
from prova.sandbox.base import Sandbox

SKIPPED_FOLDERS = frozenset({"__pycache__", ".pytest_cache", ".git", ".ruff_cache", ".mypy_cache"})


def read_text_files(root: Path) -> dict[str, str]:
    """Every file under `root` that is text (UTF-8), by its path inside `root` with `/`. Links, files that are
    not text and the folders that tools leave behind are left out."""
    found: dict[str, str] = {}
    for folder, subfolders, files in os.walk(root):  # does not go into a linked folder
        subfolders[:] = [name for name in subfolders if name not in SKIPPED_FOLDERS]
        for name in files:
            path = Path(folder) / name
            if path.is_symlink():
                continue
            try:
                found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # not text: a change to it is not shown, which only makes the feedback shorter
    return found


def run_check(sandbox: Sandbox, argv: list[str], *, timeout_s: float) -> Verification:
    """Run the check in the workspace of the sandbox and hand on its exit code, its output and whether it ran
    out of time. SandboxUnavailableError is raised as it is."""
    result = sandbox.run(argv, timeout_s=timeout_s, cwd="/work")
    return Verification(exit_code=result.exit_code, output=result.output, timed_out=result.timed_out)
