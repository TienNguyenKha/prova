"""Gitignore-style path patterns, used for the deny-lists.

Supported: `*` and `?` (never across `/`), and `**` as a whole path segment. Everything else is literal
text: there are no character classes. Matching ignores case, because a case-insensitive file system treats
`.ENV` and `.env` as one file. A pattern WITHOUT `/` matches a name at any depth; a pattern WITH `/` is
anchored at the root.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Rule:
    regex: re.Pattern[str]
    anchored: bool  # True: match the whole relative path. False: match any single path component.


class PathMatcher:
    def __init__(self, patterns: Iterable[str]) -> None:
        self._rules = [_compile(pattern) for pattern in patterns]

    def matches(self, relative_path: str) -> bool:
        """Does any pattern match this path? The path must be relative and already normalised."""
        if relative_path == "":  # the workspace root itself
            return False
        parts = relative_path.casefold().split("/")
        if relative_path.startswith("/") or any(part in ("", ".", "..") for part in parts):
            # Do not guess: a wrong answer here would be a hole in the deny-list.
            raise ValueError(
                f"path must be relative and normalised (no '..', '.' or empty parts): {relative_path!r}"
            )
        whole = "/".join(parts)
        return any(
            rule.regex.fullmatch(whole) if rule.anchored else any(rule.regex.fullmatch(p) for p in parts)
            for rule in self._rules
        )


def _compile(pattern: str) -> _Rule:
    if pattern == "":
        raise ValueError("empty pattern")
    segments = pattern.casefold().split("/")
    if "" in segments:
        raise ValueError(
            f"pattern {pattern!r} has an empty path segment (write 'dir/**' to cover a directory)"
        )
    if len(segments) == 1:  # no slash: a name, matched at any depth
        return _Rule(re.compile(_glob_segment(segments[0])), anchored=False)

    last = len(segments) - 1
    parts: list[str] = []
    for index, segment in enumerate(segments):
        if segment == "**":
            # At the end: everything inside. Elsewhere: zero or more whole directories.
            parts.append(".+" if index == last else "(?:[^/]+/)*")
        else:
            parts.append(_glob_segment(segment) + ("" if index == last else "/"))
    return _Rule(re.compile("".join(parts)), anchored=True)


def _glob_segment(segment: str) -> str:
    """Translate one path segment: `*` and `?` become wildcards that stop at `/`, the rest is literal."""
    out: list[str] = []
    i = 0
    while i < len(segment):
        char = segment[i]
        if char == "*":
            while i + 1 < len(segment) and segment[i + 1] == "*":
                i += 1  # `**` inside a name is just `*`
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)
