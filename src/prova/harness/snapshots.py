"""SnapshotStore: remember a file as it was before the run first wrote to it, and give it back.

The copies live in memory, so they are lost if the process dies. Nothing here protects a workspace that is
not a disposable copy; the sandbox (M7) is what will.
"""

from dataclasses import dataclass
from pathlib import Path

from prova.errors import SnapshotError


@dataclass(frozen=True, slots=True)
class _Entry:
    original: bytes | None  # the file's bytes before the first write; None: the file did not exist
    made_dirs: tuple[Path, ...]  # directories that did not exist either, outermost first


class SnapshotStore:
    def __init__(self) -> None:
        self._entries: dict[Path, _Entry] = {}

    @property
    def paths(self) -> tuple[Path, ...]:
        """The tracked files, in the order they were first touched."""
        return tuple(self._entries)

    def ensure(self, path: Path) -> None:
        """Call this right before the first write to `path`. Later calls for the same path do nothing, so the
        copy is always the ORIGINAL, not the result of an earlier edit."""
        if path in self._entries:
            return
        if path.is_dir():
            raise SnapshotError(f"cannot snapshot a directory: {path}")
        if path.exists():
            try:
                self._entries[path] = _Entry(path.read_bytes(), ())
            except OSError as e:
                raise SnapshotError(f"cannot snapshot {path}: {e.strerror or e}") from e
            return
        missing: list[Path] = []
        for parent in path.parents:
            if parent.exists():
                break
            missing.append(parent)
        self._entries[path] = _Entry(None, tuple(reversed(missing)))

    def rollback(self, path: Path) -> None:
        """Put `path` back as it was: restore its bytes, or remove it (and the directories made for it)."""
        entry = self._entries.get(path)
        if entry is None:
            raise SnapshotError(f"{path} is not tracked: there is nothing to roll back")
        try:
            if entry.original is None:
                path.unlink(missing_ok=True)
                _remove_empty_dirs(entry.made_dirs)
            else:
                path.write_bytes(entry.original)
        except OSError as e:
            raise SnapshotError(f"cannot restore {path}: {e.strerror or e}") from e
        del self._entries[path]  # only after it worked: a failed restore stays tracked, so it can be retried

    def rollback_all(self) -> None:
        """Undo everything, latest first. A failure does not stop the rest; they are reported together."""
        failures: list[str] = []
        for path in reversed(self.paths):
            try:
                self.rollback(path)
            except SnapshotError as e:
                failures.append(str(e))
        if failures:
            raise SnapshotError("; ".join(failures))

    def abandon(self, path: Path) -> None:
        """The write after `ensure` did not happen. Forget `path` and remove the directories made for it, but
        never touch the file: it may not be ours (someone else made it in the meantime)."""
        entry = self._entries.pop(path, None)
        if entry is not None and entry.original is None:
            _remove_empty_dirs(entry.made_dirs)

    def discard(self) -> None:
        """The run succeeded: forget every snapshot and leave the files as they are."""
        self._entries.clear()


def _remove_empty_dirs(directories: tuple[Path, ...]) -> None:
    """Remove the directories we made, innermost first. One that holds something else is left, and so is every
    directory around it."""
    for directory in reversed(directories):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue  # it was never made, or it is already gone
        except OSError:
            break  # not empty: someone else put something in it
