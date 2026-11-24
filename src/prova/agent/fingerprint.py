"""The fingerprint of a failure: how a loop that fixes and tests again knows it is going round in a circle.

It is made of which tests failed and with what kind of error, and of nothing that changes from one run to the
next: not line numbers, not temporary folders, not times, not the values in a message. The same mistake made
twice gives the same fingerprint.

Output that does not show which tests failed gives no fingerprint (None), never the fingerprint of "nothing".
"No failure to compare" must not look like "the same failure as before". And only the short summary at the end
of pytest's output is believed: a test can print any line it likes earlier, such as `FAILED x::y - KeyError`.
"""

import hashlib
import re
from dataclasses import dataclass

_SUMMARY = re.compile(r"^=+ short test summary info =+$")
_ANY_BANNER = re.compile(r"^=+ .* =+$")
_COLLECTING = re.compile(r"^_+ ERROR collecting (\S+) _+$")
_LINE_OF_A_SECTION = re.compile(r"^[_=]{3,}")
_E_LINE = re.compile(r"^E\s+([A-Za-z_][\w.]*):")
_KIND = re.compile(r"^([A-Za-z_][\w.]*):")
_PATH = re.compile(r"(?<![\w.\]])/[^\s\[\]]+(?:/[^\s\[\]]+)+")  # an absolute path of two parts or more


@dataclass(frozen=True, slots=True)
class Failure:
    """One line of pytest's short summary: a test that failed or could not be collected."""

    status: str  # FAILED or ERROR
    test: str  # the test id, with an absolute path in it replaced by <path>
    kind: str  # AssertionError, KeyError, ...; "none" if pytest gave no message, "other" if of no known form
    message: str  # what pytest wrote after the id, as it wrote it (it can be cut by pytest)


def parse_failures(output: str) -> list[Failure] | None:
    """The failures that the short summary of pytest's output lists, or None if the output shows none.

    None covers a run that passed, a run that found no test and output that is not pytest's, and it is
    not an empty list: "no failure shown" must never be taken for "a failure seen twice". Only the last
    summary is believed, and a line after the banner that ends it is not.
    """
    lines = output.splitlines()
    start = max((i for i, line in enumerate(lines) if _SUMMARY.match(line.strip())), default=None)
    if start is None:
        return None
    collecting = _collecting_errors(lines[:start])
    found: list[Failure] = []
    for line in lines[start + 1 :]:
        if _ANY_BANNER.match(line.strip()):
            break  # the end of the summary
        failure = _failure(line, collecting)
        if failure is not None:
            found.append(failure)
    return found or None


def failure_fingerprint(output: str) -> str | None:
    """A sha256 of the failed tests and the kind of error of each, or None if the output shows none."""
    failures = parse_failures(output)
    return None if failures is None else fingerprint_of(failures)


def fingerprint_of(failures: list[Failure]) -> str:
    keys = sorted({f"{f.status}|{f.test}|{f.kind}" for f in failures})
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def _failure(line: str, collecting: dict[str, str]) -> Failure | None:
    status, _, rest = line.partition(" ")
    if status not in ("FAILED", "ERROR") or not rest.strip():
        return None
    name, separator, message = rest.strip().partition(" - ")
    test = _PATH.sub("<path>", name)
    if separator:
        kind = _kind(message)
    elif status == "ERROR":
        kind = collecting.get(rest.strip(), "error")  # a test that could not be collected has no message here
    else:
        kind = "none"
    return Failure(status, test, kind, message.strip() if separator else "")


def _kind(message: str) -> str:
    text = message.strip()
    if text.startswith("assert"):
        return "AssertionError"  # pytest writes the failed expression and not the name of the error
    found = _KIND.match(text)
    return found.group(1) if found else "other"


def _collecting_errors(lines: list[str]) -> dict[str, str]:
    """For each file that could not be collected, the kind of error from the `E   Kind: ...` line under it."""
    kinds: dict[str, str] = {}
    current: str | None = None
    for line in lines:
        header = _COLLECTING.match(line.strip())
        if header:
            name: str = header.group(1)
            current = name
            kinds.setdefault(name, "error")
        elif _LINE_OF_A_SECTION.match(line):
            current = None
        elif current is not None:
            found = _E_LINE.match(line)
            if found and kinds[current] == "error":
                kinds[current] = found.group(1)
    return kinds
