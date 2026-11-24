"""The workspace the agent works in, and what it changed there.

The agent gets a copy of repo/ and nothing else of the task. What it changed is found by comparing files with
the original, never by running git in its folder: the agent's code runs in a sandbox with this folder mounted,
so it could rewrite `.git/config` or a hook, and the host must not run anything the agent chose. For the same
reason a symlink is never followed, and a file that is not small text is listed but not read into the patch.
"""

import difflib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

MAX_TEXT_BYTES = (
    1_000_000  # a bigger file is listed, not diffed: the agent must not be able to fill our memory
)
_IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache"})
_IGNORED_NAMES = frozenset({".coverage"})


@dataclass(frozen=True, slots=True)
class Changes:
    files: tuple[str, ...]  # the files that differ from the original, sorted, with "/" in the path
    patch: str  # a unified diff of the text files; a line each for the others
    added: int  # lines added and removed, over the text files
    removed: int


def prepare_workspace(task_dir: Path, workspace: Path) -> None:
    """Give the agent a copy of the task's repo/ in `workspace`, which must not exist yet."""
    if workspace.exists():
        raise FileExistsError(f"{workspace} already exists: a run gets a workspace of its own")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".coverage")
    shutil.copytree(task_dir / "repo", workspace, ignore=ignore, symlinks=True)


def collect_changes(original: Path, workspace: Path) -> Changes:
    """What `workspace` has that `original` does not, as a patch and as numbers. Reads files, runs nothing."""
    before, after = _entries(original), _entries(workspace)
    files: list[str] = []
    patch: list[str] = []
    added = removed = 0
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old is not None and new is not None and old.is_symlink() == new.is_symlink() and _same(old, new):
            continue
        files.append(name)
        text, plus, minus = _describe(name, old, new)
        patch.append(text)
        added, removed = added + plus, removed + minus
    return Changes(files=tuple(files), patch="".join(patch), added=added, removed=removed)


def _entries(root: Path) -> dict[str, Path]:
    """Every file and symlink under `root`, by relative path, without going into symlinked folders."""
    found: dict[str, Path] = {}
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _IGNORED_PARTS]
        base = Path(folder)
        linked_dirs = [d for d in dirs if (base / d).is_symlink()]
        for name in [*names, *linked_dirs]:
            if name in _IGNORED_NAMES or name.endswith(".pyc"):
                continue
            path = base / name
            found[path.relative_to(root).as_posix()] = path
    return found


def _same(old: Path, new: Path) -> bool:
    if old.is_symlink():
        return old.readlink() == new.readlink()
    return old.read_bytes() == new.read_bytes()


def _describe(name: str, old: Path | None, new: Path | None) -> tuple[str, int, int]:
    """The patch text for one changed file, and the lines it adds and removes."""
    if any(p is not None and p.is_symlink() for p in (old, new)):
        return (
            f"Symlink or file type changed: {name}\n",
            0,
            0,
        )  # never followed: its target is not ours to read
    old_bytes = old.read_bytes() if old is not None else None
    new_bytes = new.read_bytes() if new is not None else None
    sizes = [len(b) for b in (old_bytes, new_bytes) if b is not None]
    if max(sizes) > MAX_TEXT_BYTES:
        return f"File {name} is too large to show ({max(sizes)} bytes)\n", 0, 0
    old_lines, new_lines = _lines(old_bytes), _lines(new_bytes)
    if old_lines is None or new_lines is None:
        return f"Binary file {name} differs\n", 0, 0
    source = f"a/{name}" if old is not None else "/dev/null"
    target = f"b/{name}" if new is not None else "/dev/null"
    diff = list(difflib.unified_diff(old_lines, new_lines, source, target))
    body = diff[2:]  # the first two lines are the headers
    plus = sum(1 for line in body if line.startswith("+"))
    minus = sum(1 for line in body if line.startswith("-"))
    return (
        "".join(line if line.endswith("\n") else f"{line}\n\\ No newline at end of file\n" for line in diff),
        plus,
        minus,
    )


def _lines(data: bytes | None) -> list[str] | None:
    """The lines of a text file; [] for a file that does not exist; None when it is not text."""
    if data is None:
        return []
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None
