"""The tools that change files: edit_file (one exact replacement) and create_file (a new file only).

Every failure says what to do next."""

import difflib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from prova.errors import ErrorCode, ToolError
from prova.tools.base import Permission, RunContext, ToolResult
from prova.tools.fs import decode_text, number_lines
from prova.tools.paths import resolve_inside
from prova.tools.text import count_noun

DEFAULT_EDIT_BYTES = 200_000  # the largest file edit_file will rewrite; the error states the limit
CONTEXT_LINES = 2  # lines of context around the edit in the result
MAX_LISTED_LOCATIONS = 10  # an ambiguous match lists this many places, then says how many more
MAX_QUOTED_CHARS = 200  # how much of the closest match is quoted back to the model
CLOSE_ENOUGH = 0.6  # similarity (0 to 1) from which a near miss is worth showing

_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+│ ")  # what read_file puts in front of every line


def _resolve_target(path: str, ctx: RunContext) -> tuple[Path, str]:
    """The real location of `path` and its path inside the workspace, or a ToolError (outside, protected)."""
    target = resolve_inside(ctx.workspace, path, deny=ctx.deny)
    return target, target.relative_to(ctx.workspace.resolve()).as_posix()


def _require_writable(relative: str, path: str, ctx: RunContext) -> None:
    """Before anything is read or made: a path outside the write paths must not reveal whether it exists."""
    if not ctx.write.matches(relative):
        raise ToolError(ErrorCode.PERMISSION_DENIED, f"{path} is not writable in this run")


class EditFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path of the file, relative to the workspace root")
    old_str: str = Field(
        min_length=1,
        description="The exact text to replace. It must appear in the file exactly once",
    )
    new_str: str = Field(description="The text to put in its place (may be empty, to delete)")


class EditFileTool:
    name = "edit_file"
    description = (
        "Replace one exact piece of text in an existing file. old_str must match the file exactly ONCE, "
        "including spaces and indentation, and must NOT include the line numbers that read_file shows. "
        "If it matches in several places, add surrounding lines to make it unique. Read the file first."
    )
    args_model = EditFileArgs
    permission = Permission.WRITE

    def __init__(self, *, max_bytes: int = DEFAULT_EDIT_BYTES) -> None:
        self._max_bytes = max_bytes

    def run(self, args: EditFileArgs, ctx: RunContext) -> ToolResult:
        target, relative = _resolve_target(args.path, ctx)
        if relative == ".":
            raise ToolError(ErrorCode.INVALID_ARGS, f"{args.path} is a directory, not a file")
        _require_writable(relative, args.path, ctx)
        if args.old_str == args.new_str:
            message = "old_str and new_str are the same: there is nothing to change"
            raise ToolError(ErrorCode.INVALID_ARGS, message)
        if not target.exists():
            raise ToolError(ErrorCode.NOT_FOUND, f"no such file: {args.path} (to make a new file, create it)")
        if not target.is_file():
            raise ToolError(ErrorCode.INVALID_ARGS, f"{args.path} is not a regular file")
        size = target.stat().st_size
        if size > self._max_bytes:
            message = f"{args.path} is {size} bytes; edit_file works on files up to {self._max_bytes} bytes"
            raise ToolError(ErrorCode.INVALID_ARGS, message)

        try:
            text = decode_text(target.read_bytes(), args.path, final=True)
        except PermissionError:
            message = f"cannot read {args.path}: permission denied"
            raise ToolError(ErrorCode.PERMISSION_DENIED, message) from None

        old, new = args.old_str, args.new_str
        if "\r\n" in text:  # the model writes plain newlines; a CRLF file needs them in its own form
            old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            new = new.replace("\r\n", "\n").replace("\n", "\r\n")

        starts = _find_all(text, old)
        if not starts:
            raise _no_match(args.path, text, old)
        if len(starts) > 1:
            raise _ambiguous(text, starts)

        start = starts[0]
        edited = text[:start] + new + text[start + len(old) :]
        ctx.snapshots.ensure(target)  # the original, before the first write to this file
        try:
            target.write_bytes(edited.encode("utf-8"))
        except PermissionError:
            message = f"cannot write {args.path}: permission denied"
            raise ToolError(ErrorCode.PERMISSION_DENIED, message) from None

        first = _line_of(text, start)
        old_lines, new_lines = len(old.splitlines()) or 1, len(new.splitlines())
        replaced_with = "nothing" if new_lines == 0 else count_noun(new_lines, "line", "lines")
        message = f"edited {args.path}: replaced {_span(first, old_lines)} with {replaced_with}"
        data = {"path": args.path, "line": first, "old_lines": old_lines, "new_lines": new_lines}
        return ToolResult.ok(message, data=data, snapshot=_region(edited, first, new_lines))


def _find_all(text: str, needle: str) -> list[int]:
    starts: list[int] = []
    at = text.find(needle)
    while at != -1:
        starts.append(at)
        at = text.find(needle, at + len(needle))
    return starts


def _line_of(text: str, index: int) -> int:
    """The line number (counted like splitlines) of the character at `index`."""
    before = text[:index].splitlines(keepends=True)
    if before and before[-1].splitlines()[0] == before[-1]:  # the last piece has no line break: same line
        return len(before)
    return len(before) + 1


def _span(first: int, count: int) -> str:
    return f"line {first}" if count == 1 else f"lines {first}-{first + count - 1}"


def _region(edited: str, first: int, new_lines: int) -> str | None:
    """The edited lines with a little context, numbered, so the model can check without a re-read."""
    lines = edited.splitlines()
    begin = max(1, first - CONTEXT_LINES)
    end = min(len(lines), first + max(new_lines, 1) - 1 + CONTEXT_LINES)
    if end < begin:
        return None
    return number_lines("\n".join(lines[begin - 1 : end]), start=begin)


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _ambiguous(text: str, starts: list[int]) -> ToolError:
    lines = [_line_of(text, start) for start in starts]
    listed = ", ".join(str(n) for n in lines[:MAX_LISTED_LOCATIONS])
    more = f", and {len(lines) - MAX_LISTED_LOCATIONS} more" if len(lines) > MAX_LISTED_LOCATIONS else ""
    message = (
        f"old_str matches {len(lines)} locations (lines {listed}{more}). "
        "Include more surrounding lines to make it unique."
    )
    return ToolError(ErrorCode.AMBIGUOUS, message)


def _no_match(path: str, text: str, old: str) -> ToolError:
    old_lines = old.splitlines()
    if old_lines and all(_LINE_NUMBER_PREFIX.match(line) for line in old_lines):
        message = (
            "old_str starts its lines with line numbers. The line numbers shown by read_file are not part of "
            "the file: copy only the text after the bar."
        )
        return ToolError(ErrorCode.INVALID_ARGS, message)

    file_lines = text.splitlines()
    size = len(old_lines) or 1
    windows = [(i + 1, "\n".join(file_lines[i : i + size])) for i in range(len(file_lines) - size + 1)]

    same_apart_from_spaces = [number for number, window in windows if _normalised(window) == _normalised(old)]
    if same_apart_from_spaces and _normalised(old):
        where = ", ".join(str(n) for n in same_apart_from_spaces[:MAX_LISTED_LOCATIONS])
        word = "line" if len(same_apart_from_spaces) == 1 else "lines"
        message = (
            f"old_str matches after whitespace normalisation at {word} {where}. "
            "Copy the spaces and indentation exactly as read_file shows them."
        )
        return ToolError(ErrorCode.WHITESPACE_MISMATCH, message)

    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(old)  # set_seq2 is the one SequenceMatcher caches work for
    best_score, best = 0.0, None
    for number, window in windows:
        matcher.set_seq1(window)
        if matcher.real_quick_ratio() <= best_score or matcher.quick_ratio() <= best_score:
            continue
        score = matcher.ratio()
        if score > best_score:
            best_score, best = score, (number, window)

    message = f"old_str not found in {path}."
    if best is not None and best_score >= CLOSE_ENOUGH:
        number, window = best
        quoted = window if len(window) <= MAX_QUOTED_CHARS else window[:MAX_QUOTED_CHARS] + "... (cut)"
        message += f" Closest match at {_span(number, size)}: {quoted!r}."
    message += " Re-read the file before editing."
    return ToolError(ErrorCode.NO_MATCH, message)


class CreateFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path of the new file, relative to the workspace root")
    content: str = Field(description="The whole content of the new file (may be empty)")


class CreateFileTool:
    name = "create_file"
    description = (
        "Create a NEW file with the given content. It fails if the file already exists: to change an "
        "existing file, use edit_file. Missing directories on the path are created."
    )
    args_model = CreateFileArgs
    permission = Permission.WRITE

    def __init__(self, *, max_bytes: int = DEFAULT_EDIT_BYTES) -> None:
        self._max_bytes = max_bytes

    def run(self, args: CreateFileArgs, ctx: RunContext) -> ToolResult:
        target, relative = _resolve_target(args.path, ctx)
        if relative == ".":
            raise ToolError(ErrorCode.ALREADY_EXISTS, "the workspace root is a directory: it already exists")
        _require_writable(relative, args.path, ctx)

        if "\x00" in args.content:
            message = "the content has NUL characters: only text files can be created"
            raise ToolError(ErrorCode.INVALID_ARGS, message)
        try:
            data = args.content.encode("utf-8")
        except UnicodeEncodeError:
            raise ToolError(ErrorCode.INVALID_ARGS, "the content cannot be written as UTF-8 text") from None
        if len(data) > self._max_bytes:
            message = f"the content is {len(data)} bytes; create_file takes up to {self._max_bytes} bytes"
            raise ToolError(ErrorCode.INVALID_ARGS, message)
        if target.exists():
            message = f"{args.path} already exists: create_file never overwrites. Use edit_file to change it"
            raise ToolError(ErrorCode.ALREADY_EXISTS, message)

        ctx.snapshots.ensure(target)  # before the directories are made, so it knows which ones are new
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            ctx.snapshots.abandon(target)
            message = f"cannot create {args.path}: a directory on its path is really a file"
            raise ToolError(ErrorCode.INVALID_ARGS, message) from None
        try:
            with target.open("xb") as f:  # exclusive: if the file appeared since the check, this fails
                f.write(data)
        except FileExistsError:
            ctx.snapshots.abandon(target)
            message = f"{args.path} already exists: create_file never overwrites. Use edit_file to change it"
            raise ToolError(ErrorCode.ALREADY_EXISTS, message) from None
        except PermissionError:
            ctx.snapshots.abandon(target)
            message = f"cannot write {args.path}: permission denied"
            raise ToolError(ErrorCode.PERMISSION_DENIED, message) from None

        lines = len(args.content.splitlines())
        counted = f"{count_noun(lines, 'line', 'lines')}, {count_noun(len(data), 'byte', 'bytes')}"
        return ToolResult.ok(
            f"created {args.path} ({counted})",
            data={"path": args.path, "lines": lines, "bytes": len(data)},
        )
