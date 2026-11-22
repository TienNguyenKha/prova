"""Filesystem tools confined to the workspace."""

import codecs
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from prova.errors import ErrorCode, ToolError
from prova.tools.base import Permission, RunContext, ToolResult
from prova.tools.paths import resolve_inside
from prova.tools.text import count_noun

DEFAULT_SNAPSHOT_BYTES = 20_000  # how much of a file the model gets to see; the cut is always stated
DEFAULT_LIST_ENTRIES = 200  # how many directory entries the model gets to see; the cut is always stated


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path of the file, relative to the workspace root")


class ReadFileTool:
    name = "read_file"
    description = (
        "Read a UTF-8 text file inside the workspace. A long file is cut: the result says how much was shown."
    )
    args_model = ReadFileArgs
    permission = Permission.READ

    def __init__(self, *, max_bytes: int = DEFAULT_SNAPSHOT_BYTES) -> None:
        self._max_bytes = max_bytes

    def run(self, args: ReadFileArgs, ctx: RunContext) -> ToolResult:
        target = resolve_inside(ctx.workspace, args.path, deny=ctx.deny)
        if not target.exists():
            raise ToolError(ErrorCode.NOT_FOUND, f"no such file: {args.path}")
        if target.is_dir():
            raise ToolError(ErrorCode.INVALID_ARGS, f"{args.path} is a directory, not a file")
        if not target.is_file():  # a named pipe or device: opening it could block forever
            raise ToolError(ErrorCode.INVALID_ARGS, f"{args.path} is not a regular file")

        try:
            size = target.stat().st_size
            with target.open("rb") as f:
                raw = f.read(self._max_bytes)  # never read more than we may show
        except PermissionError:
            message = f"cannot read {args.path}: permission denied"
            raise ToolError(ErrorCode.PERMISSION_DENIED, message) from None

        truncated = size > self._max_bytes
        text = self._decode(raw, args.path, final=not truncated)
        data = {"path": args.path, "bytes": size, "truncated": truncated}

        if truncated:
            shown = len(text.encode("utf-8"))
            note = f"cut: showing the first {shown} of {size} bytes"
            snapshot = f"{text}\n[{note}; the rest is not shown]"
            return ToolResult.ok(f"read {args.path}: {note}", data=data, snapshot=snapshot)
        if not text:
            return ToolResult.ok(f"read {args.path} (empty file)", data=data)
        lines = text.count("\n") + (0 if text.endswith("\n") else 1)
        counted = f"{count_noun(lines, 'line', 'lines')}, {count_noun(size, 'byte', 'bytes')}"
        return ToolResult.ok(f"read {args.path} ({counted})", data=data, snapshot=text)

    @staticmethod
    def _decode(raw: bytes, path: str, *, final: bool) -> str:
        # An incremental decoder keeps a character that the cut split in two as pending instead of failing,
        # but still rejects bytes that are really not UTF-8. `final` is true when we hold the whole file.
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            text = decoder.decode(raw, final=final)
        except UnicodeDecodeError:
            raise ToolError(ErrorCode.INVALID_ARGS, f"{path} is not a UTF-8 text file") from None
        if "\x00" in text:
            raise ToolError(ErrorCode.INVALID_ARGS, f"{path} looks like a binary file (has NUL bytes)")
        return text


class ListDirArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        default=".",
        description="Directory to list, relative to the workspace root (default: the root itself)",
    )


class ListDirTool:
    name = "list_dir"
    description = (
        "List the entries of a directory inside the workspace, sorted by name. "
        "A long listing is cut: the result says how many entries were shown."
    )
    args_model = ListDirArgs
    permission = Permission.READ

    def __init__(self, *, max_entries: int = DEFAULT_LIST_ENTRIES) -> None:
        self._max_entries = max_entries

    def run(self, args: ListDirArgs, ctx: RunContext) -> ToolResult:
        target = resolve_inside(ctx.workspace, args.path, deny=ctx.deny)
        if not target.exists():
            raise ToolError(ErrorCode.NOT_FOUND, f"no such directory: {args.path}")
        if not target.is_dir():
            message = f"{args.path} is a file, not a directory; use read_file to read it"
            raise ToolError(ErrorCode.INVALID_ARGS, message)

        workspace = ctx.workspace.resolve()
        try:
            # Protected entries are hidden completely: not listed, not counted. Saying "2 entries are hidden"
            # would only invite the model to try to read them.
            names = ((e, e.relative_to(workspace).as_posix()) for e in target.iterdir())
            allowed = (entry for entry, relative in names if not ctx.deny.matches(relative))
            entries = sorted(allowed, key=lambda entry: entry.name)
        except PermissionError:
            message = f"cannot list {args.path}: permission denied"
            raise ToolError(ErrorCode.PERMISSION_DENIED, message) from None

        total = len(entries)
        shown = entries[: self._max_entries]
        truncated = total > len(shown)
        data = {"path": args.path, "entries": total, "truncated": truncated}

        if total == 0:
            return ToolResult.ok(f"{args.path} is an empty directory", data=data)
        snapshot = "\n".join(self._describe(entry) for entry in shown)
        if truncated:
            note = f"cut: showing the first {len(shown)} of {total} entries"
            return ToolResult.ok(f"listed {args.path}: {note}", data=data, snapshot=f"{snapshot}\n[{note}]")
        message = f"listed {args.path} ({count_noun(total, 'entry', 'entries')})"
        return ToolResult.ok(message, data=data, snapshot=snapshot)

    @staticmethod
    def _describe(entry: Path) -> str:
        if entry.is_symlink():  # shown, never followed: its target could be outside the workspace
            return f"{entry.name} (symlink)"
        if entry.is_dir():
            return f"{entry.name}/"
        return f"{entry.name} ({count_noun(entry.stat().st_size, 'byte', 'bytes')})"
