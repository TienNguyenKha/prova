"""Reading a unified diff: the lines each file got and lost.

The header of a hunk says how many lines it has, and that is what ends the hunk. So a removed line whose text
starts with `-- ` is a line and not a file header, and an added line that starts with `++ ` is not either.
"""

import re
from dataclasses import dataclass

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True, slots=True)
class FilePatch:
    added: tuple[tuple[int, str], ...]  # (number of the line in the new file, its text)
    removed: tuple[str, ...]  # the text of each line that is gone


def parse_patch(patch: str) -> dict[str, FilePatch]:
    """The added and removed lines of each file with a text change. Notes about binary files are not lines."""
    added: dict[str, list[tuple[int, str]]] = {}
    removed: dict[str, list[str]] = {}
    old_path: str | None = None
    new_path: str | None = None
    lines = iter(patch.splitlines())
    for line in lines:
        if line.startswith("--- "):
            old_path = _path(line[4:], "a/")
            continue
        if line.startswith("+++ "):
            new_path = _path(line[4:], "b/")
            continue
        header = _HUNK.match(line)
        if header is None:
            continue
        key = new_path or old_path
        old_left = int(header.group(1) or 1)
        new_no, new_left = int(header.group(2)), int(header.group(3) or 1)
        while old_left > 0 or new_left > 0:  # the counts of the header say where the hunk ends
            body = next(lines, None)
            if body is None:
                break
            if body.startswith("\\"):  # "\ No newline at end of file"
                continue
            kind, text = body[:1], body[1:]
            if kind == "-":
                old_left -= 1
                if key is not None:
                    removed.setdefault(key, []).append(text)
                continue
            new_left -= 1
            if kind == "+":
                if key is not None:
                    added.setdefault(key, []).append((new_no, text))
            else:
                old_left -= 1  # a context line, with or without its leading space
            new_no += 1
    return {
        path: FilePatch(added=tuple(added.get(path, ())), removed=tuple(removed.get(path, ())))
        for path in sorted(added.keys() | removed.keys())
    }


def _path(name: str, prefix: str) -> str | None:
    name = name.split("\t")[0]
    return None if name == "/dev/null" else name.removeprefix(prefix)
