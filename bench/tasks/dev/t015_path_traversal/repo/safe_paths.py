"""Join a path given by a user to a base folder, and refuse a path that would lead out of it."""

import os


class UnsafePathError(ValueError):
    """The path given by the user leads out of the base folder."""


def safe_join(base: str, user_path: str) -> str:
    """The path of `user_path` inside the folder `base`. Raises UnsafePathError if it leads out of `base`."""
    path = os.path.normpath(os.path.join(base, user_path))  # noqa: PTH118 - this code works on text
    if not path.startswith(os.path.normpath(base)):
        raise UnsafePathError(f"{user_path!r} leads out of {base!r}")
    return path
