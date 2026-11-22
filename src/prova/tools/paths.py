"""Keep file tools inside the workspace, and away from protected files."""

from pathlib import Path

from prova.errors import ErrorCode, ToolError
from prova.harness.patterns import PathMatcher


def resolve_inside(root: Path, path: str, *, deny: PathMatcher | None = None) -> Path:
    """Return the real location of `path` under `root`, or raise ToolError if it may not be touched.

    It may not be touched when it points outside `root`, or when its REAL location (after `..` and symlinks)
    matches the `deny` patterns. A protected path is refused whether or not the file exists: "protected"
    for one file and "not found" for another would tell the model which secrets are there.
    """
    resolved_root = root.resolve()
    try:
        # resolve() follows `..` and symlinks, so the checks below see where the path REALLY points.
        # An absolute `path` replaces the root in the join, then fails the check unless it is inside the root.
        target = (resolved_root / path).resolve()
    except ValueError:  # for example a NUL character: the model sent something that is not a path
        raise ToolError(ErrorCode.INVALID_ARGS, "the path contains characters a path cannot have") from None
    # is_relative_to compares path components; a string prefix check would let `work_evil` pass for `work`.
    if not target.is_relative_to(resolved_root):
        raise ToolError(ErrorCode.PERMISSION_DENIED, f"path escapes the workspace: {path}")
    relative = target.relative_to(resolved_root).as_posix()
    if deny is not None and relative != "." and deny.matches(relative):  # "." is the workspace root itself
        raise ToolError(ErrorCode.PERMISSION_DENIED, f"{path} is protected and cannot be accessed")
    return target
