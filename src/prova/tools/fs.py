"""Filesystem tools confined to a root directory. M2 version: one tool, a tiny dispatcher, no registry."""

from collections.abc import Mapping
from pathlib import Path

from anthropic.types import ToolParam

from prova.errors import ErrorCode, ToolError
from prova.llm.tool_messages import ToolRunner

DEFAULT_MAX_BYTES = 100_000  # a huge file would inflate input_tokens on every later turn

READ_FILE_TOOL: ToolParam = {
    "name": "read_file",
    "description": "Read a UTF-8 text file inside the workspace. Returns its content.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path relative to workspace root"}},
        "required": ["path"],
    },
}
FS_TOOLS: list[ToolParam] = [READ_FILE_TOOL]


def read_file(root: Path, path: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    """Read a UTF-8 text file that lives inside `root`.

    Failures the model can fix (outside the root, missing, directory, too large, not UTF-8) are ToolErrors.
    OS-level read errors such as a permission error are not mapped yet and propagate (see the TODO below).
    """
    resolved_root = root.resolve()
    # resolve() follows `..` and symlinks, so the check below sees where the path REALLY points.
    # An absolute `path` replaces the root in the join, then fails the check unless it is inside the root.
    target = (resolved_root / path).resolve()
    # is_relative_to compares path components; a string prefix check would let `work_evil` pass for `work`.
    if not target.is_relative_to(resolved_root):
        raise ToolError(ErrorCode.PERMISSION_DENIED, f"path escapes the workspace: {path}")
    if not target.exists():
        raise ToolError(ErrorCode.NOT_FOUND, f"no such file: {path}")
    if target.is_dir():
        raise ToolError(ErrorCode.INVALID_ARGS, f"{path} is a directory, not a file")
    size = target.stat().st_size
    if size > max_bytes:
        raise ToolError(ErrorCode.INVALID_ARGS, f"file too large: {size} bytes (limit {max_bytes})")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError(ErrorCode.INVALID_ARGS, f"{path} is not a UTF-8 text file") from None
    # TODO(M4): an unreadable file (OS permission error) is not mapped to a ToolError yet.


def make_fs_runner(root: Path) -> ToolRunner:
    """Build the tool runner for the filesystem tools, bound to `root`."""

    def run(name: str, tool_input: Mapping[str, object]) -> str:
        if name == "read_file":
            path = tool_input.get("path")
            if not isinstance(path, str):
                raise ToolError(ErrorCode.INVALID_ARGS, "read_file needs a string argument 'path'")
            return read_file(root, path)
        raise ToolError(ErrorCode.NOT_FOUND, f"unknown tool {name!r}")

    return run
