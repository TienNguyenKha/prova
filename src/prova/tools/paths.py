"""Keep file tools inside the workspace."""

from pathlib import Path

from prova.errors import ErrorCode, ToolError


def resolve_inside(root: Path, path: str) -> Path:
    """Return the real location of `path` under `root`, or raise ToolError if it points anywhere else."""
    resolved_root = root.resolve()
    try:
        # resolve() follows `..` and symlinks, so the check below sees where the path REALLY points.
        # An absolute `path` replaces the root in the join, then fails the check unless it is inside the root.
        target = (resolved_root / path).resolve()
    except ValueError:  # for example a NUL character: the model sent something that is not a path
        raise ToolError(ErrorCode.INVALID_ARGS, "the path contains characters a path cannot have") from None
    # is_relative_to compares path components; a string prefix check would let `work_evil` pass for `work`.
    if not target.is_relative_to(resolved_root):
        raise ToolError(ErrorCode.PERMISSION_DENIED, f"path escapes the workspace: {path}")
    return target
