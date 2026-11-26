"""Does a reproduction test fail for the right reason? Read from the output of pytest run with `--tb=native`.

A test written to show a bug is worth something only if it fails on the code as it is, and fails because of
the bug: an assertion about what the code does (or an exception that was expected and did not come), or an
exception raised in the code, even from a library that the code called. Everything else reproduces nothing: a
test that cannot be imported or does not parse, that finds nothing or passes, or that breaks on its own typo.
Output that was cut, or whose failures cannot be found, is not read: it is never taken for a reproduction.

Which tests failed comes from pytest's short summary (see prova.agent.fingerprint); where each one failed
comes from the last traceback of its own section, before any output it printed.
"""

import re
from dataclasses import dataclass
from typing import Literal

from prova.agent.fingerprint import Failure, parse_failures

Kind = Literal[
    "assertion",
    "exception_in_code",
    "exception_in_test",
    "import_error",
    "syntax_error",
    "collection_error",
    "passes",
    "no_tests",
    "unreadable",
]
REPRO_DIR = "repro"  # where a reproduction run writes its tests (the `repro` profile may write only there)
# How the reproduction tests are run, by the stop hook and by the harness: native tracebacks show where an
# error was raised; no -x, so every test is seen.
REPRO_ARGV = ["python", "-m", "pytest", "-q", "--tb=native", "-p", "no:cacheprovider", "--", REPRO_DIR]
VALID_KINDS = frozenset({"assertion", "exception_in_code"})
_FAILED_BY_ASSERTION = frozenset({"AssertionError", "Failed"})  # Failed: pytest.fail, DID NOT RAISE
_IMPORT = frozenset({"ImportError", "ModuleNotFoundError"})
_SYNTAX = frozenset({"SyntaxError", "IndentationError", "TabError"})

_HEADER = re.compile(r"^_{3,} (.+?) _{3,}$")
_CAPTURED = re.compile(r"^-{3,} Captured ")
_FRAME = re.compile(r'^\s+File "(.+?)", line \d+, in ')
_TRACEBACK = "Traceback (most recent call last):"


@dataclass(frozen=True, slots=True)
class ReproTest:
    test: str
    kind: Kind
    detail: str


@dataclass(frozen=True, slots=True)
class ReproCheck:
    valid: bool
    kind: Kind  # the first problem if not valid, else the kind of the first failure
    reason: str  # what the agent is told
    tests: tuple[ReproTest, ...] = ()


def classify_repro(output: str, *, exit_code: int, truncated: bool, root: str) -> ReproCheck:
    """Whether the run of the reproduction tests, in a workspace mounted at `root`, shows the bug."""
    if truncated:
        return _invalid("unreadable", "the output of the tests was cut, so it cannot be read")
    if exit_code == 0:
        return _invalid("passes", "every test passes on the code as it is: it reproduces nothing")
    if exit_code == 5:  # noqa: PLR2004 - pytest: no test collected
        return _invalid("no_tests", "pytest found no test to run")
    if exit_code not in (1, 2):
        return _invalid("unreadable", f"pytest ended with exit code {exit_code}, which is not a test result")
    failures = parse_failures(output)
    if failures is None:
        return _invalid("unreadable", "the output does not show which tests failed")
    sections = _sections(output)
    tests = tuple(_classify(f, sections, root.rstrip("/")) for f in failures)
    bad = [t for t in tests if t.kind not in VALID_KINDS]
    if bad:
        reason = "; ".join(f"{t.test}: {t.detail}" for t in bad)
        return ReproCheck(valid=False, kind=bad[0].kind, reason=reason, tests=tests)
    reason = "; ".join(f"{t.test}: {t.detail}" for t in tests)
    return ReproCheck(valid=True, kind=tests[0].kind, reason=reason, tests=tests)


def _invalid(kind: Kind, reason: str) -> ReproCheck:
    return ReproCheck(valid=False, kind=kind, reason=reason)


# -- one failure ---------------------------------------------------------------------------------------


def _classify(failure: Failure, sections: dict[str, list[str]], root: str) -> ReproTest:
    path, _, name = failure.test.partition("::")
    if failure.status == "ERROR":  # could not be collected
        kind: Kind = (
            "import_error"
            if failure.kind in _IMPORT
            else "syntax_error"
            if failure.kind in _SYNTAX
            else "collection_error"
        )
        return ReproTest(failure.test, kind, f"could not be collected ({failure.kind}): fix the test file")
    section = sections.get(name.replace("::", "."))
    if section is None:
        return ReproTest(failure.test, "unreadable", "its traceback is not in the output")
    found = _last_traceback(section)
    if found is None:
        return ReproTest(failure.test, "unreadable", "its traceback cannot be read")
    frames, error = found
    error_kind = error.partition(":")[0].strip().rsplit(".", 1)[-1]
    if error_kind in _FAILED_BY_ASSERTION:
        return ReproTest(name, "assertion", f"fails on an assertion: {error}")
    ours = [f for f in frames if f.startswith(f"{root}/") and "site-packages" not in f]
    if not ours:
        return ReproTest(name, "unreadable", f"{error_kind} raised outside the repo")
    deepest = ours[-1].removeprefix(f"{root}/")
    folder = path.rpartition("/")[0]
    in_test = deepest.startswith(f"{folder}/") if folder else deepest == path
    if in_test:
        detail = f"{error_kind} raised in the test itself ({deepest}), not in the code: fix the test"
        return ReproTest(name, "exception_in_test", detail)
    return ReproTest(name, "exception_in_code", f"{error_kind} raised in the code ({deepest})")


# -- reading the output --------------------------------------------------------------------------------


def _sections(output: str) -> dict[str, list[str]]:
    """The lines of each test's section, by the name in its header, up to what the test printed."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in output.splitlines():
        header = _HEADER.match(line)
        if header:
            current = sections.setdefault(header.group(1), [])
        elif line.startswith("=") or _CAPTURED.match(line):
            current = None  # the end of the section, or the start of what the test printed
        elif current is not None:
            current.append(line)
    return sections


def _last_traceback(lines: list[str]) -> tuple[list[str], str] | None:
    """The files of the frames of the last traceback, and the line of its error."""
    starts = [i for i, line in enumerate(lines) if line == _TRACEBACK]
    if not starts:
        return None
    frames: list[str] = []
    for line in lines[starts[-1] + 1 :]:
        frame = _FRAME.match(line)
        if frame:
            frames.append(frame.group(1))
        elif line and not line[0].isspace():
            return frames, line.strip()
    return None
