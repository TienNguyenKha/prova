"""search_code: find a literal text in the files of the workspace."""

import os
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from prova.errors import ErrorCode, ToolError
from prova.tools.base import Permission, RunContext, ToolResult
from prova.tools.paths import resolve_inside
from prova.tools.text import count_noun

# Tooling noise: never searched unless the model points `path` straight at one of them.
SKIPPED_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)


class SearchCodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        min_length=1,  # an empty text would match every line
        description="Text to look for. It is matched literally: it is not a regular expression",
    )
    path: str = Field(
        default=".",
        description="File or directory to search, relative to the workspace root (default: all of it)",
    )
    glob: str | None = Field(
        default=None,
        description="Only search files whose name or relative path matches this pattern, for example '*.py'",
    )
    ignore_case: bool = Field(default=False, description="Match upper and lower case letters alike")


class SearchCodeTool:
    name = "search_code"
    description = (
        "Search the files of the workspace for a text (matched literally, not as a regular expression). "
        "Each result is 'path:line: text', sorted by path. Folders such as .git and node_modules are not "
        "searched. Every limit (number of matches, number of files, long lines) is stated in the result."
    )
    args_model = SearchCodeArgs
    permission = Permission.READ

    def __init__(
        self,
        *,
        max_matches: int = 50,
        max_files: int = 10_000,
        max_file_bytes: int = 1_000_000,
        max_line_chars: int = 200,
    ) -> None:
        self._max_matches = max_matches
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_line_chars = max_line_chars

    def run(self, args: SearchCodeArgs, ctx: RunContext) -> ToolResult:
        base = resolve_inside(ctx.workspace, args.path)
        if not base.exists():
            raise ToolError(ErrorCode.NOT_FOUND, f"no such file or directory: {args.path}")
        workspace = ctx.workspace.resolve()
        needle = args.pattern.lower() if args.ignore_case else args.pattern

        found: list[str] = []
        files_with_matches: set[str] = set()
        searched = skipped = 0
        too_many_matches = stopped_at_file_limit = False

        for file in self._files(base):
            relative = file.relative_to(workspace).as_posix()
            if args.glob and not (fnmatch(file.name, args.glob) or fnmatch(relative, args.glob)):
                continue
            if searched + skipped >= self._max_files:
                stopped_at_file_limit = True
                break
            text = self._read_text(file)
            if text is None:
                skipped += 1
                continue
            searched += 1
            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle not in (line.lower() if args.ignore_case else line):
                    continue
                if len(found) == self._max_matches:
                    too_many_matches = True
                    break
                found.append(f"{relative}:{lineno}: {self._shorten(line)}")
                files_with_matches.add(relative)
            if too_many_matches:
                break

        truncated = too_many_matches or stopped_at_file_limit
        data = {
            "pattern": args.pattern,
            "path": args.path,
            "matches": len(found),
            "files_searched": searched,
            "files_skipped": skipped,
            "truncated": truncated,
        }
        skipped_note = (
            f"; skipped {count_noun(skipped, 'file', 'files')} (binary, unreadable, a symlink or over "
            f"{self._max_file_bytes} bytes)"
            if skipped
            else ""
        )

        if truncated:
            if too_many_matches:
                note = f"cut: showing the first {count_noun(len(found), 'match', 'matches')}; there are more"
            else:
                note = f"cut: searched only the first {count_noun(searched, 'file', 'files')}"
            message = f"search for {args.pattern!r}: {note}{skipped_note}"
            snapshot = "\n".join([*found, f"[{note}]"]) if found else None
            return ToolResult.ok(message, data=data, snapshot=snapshot)
        if not found:
            looked_at = f"{count_noun(searched, 'file', 'files')} searched{skipped_note}"
            return ToolResult.ok(f"no matches for {args.pattern!r} ({looked_at})", data=data)
        counted = f"{count_noun(len(found), 'match', 'matches')} for {args.pattern!r}"
        message = f"found {counted} in {count_noun(len(files_with_matches), 'file', 'files')}{skipped_note}"
        return ToolResult.ok(message, data=data, snapshot="\n".join(found))

    @staticmethod
    def _files(base: Path) -> Iterator[Path]:
        """The files to look at, in a stable order. Symlinked directories are never entered."""
        if base.is_file():
            yield base
            return
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    def _read_text(self, file: Path) -> str | None:
        """The text of a file we are willing to search, or None for a symlink, binary or unreadable file."""
        if file.is_symlink() or not file.is_file():
            return None
        try:
            if file.stat().st_size > self._max_file_bytes:
                return None
            text = file.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):  # unreadable, or not text
            return None
        return None if "\x00" in text else text

    def _shorten(self, line: str) -> str:
        line = line.rstrip()
        if len(line) <= self._max_line_chars:
            return line
        return f"{line[: self._max_line_chars]} ... [line cut, {len(line)} chars]"
