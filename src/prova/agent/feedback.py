"""Feedback for a loop that fixes and tests again: a few lines, built from facts.

What the agent is told after a failed attempt comes from two places it cannot colour: the real change
(the files compared before and after, not what the model says it did) and the output of the tests run
after the change. It is short on purpose: a model given 400 lines of traceback loses the one line that
matters. When the output does not show which tests failed, the feedback says so and does not guess.
"""

import difflib
from collections.abc import Mapping
from dataclasses import dataclass

from prova.agent.fingerprint import Failure, fingerprint_of, parse_failures

MAX_FILES = 5  # files named in the description of a change
MAX_NEW_LINES = 3  # new lines shown for a file
MAX_FAILURES = 5  # failing tests named in the feedback
MAX_LESSONS = 3  # earlier attempts named in the feedback
TAIL_LINES = 10  # lines of unreadable output that are shown
CUT = 80
NOT_READABLE = "could not tell which tests failed from the output of the tests"

Changes = Mapping[str, tuple[str | None, str | None]]  # path -> (before, after); None is "not there"


@dataclass(frozen=True, slots=True)
class Lesson:
    """What one attempt taught: what was tried (from the files), why it failed (from the tests after)."""

    attempt: int
    tried: str
    failed_because: str
    fingerprint: (
        str | None
    )  # None when the output did not show the failure: it is never "the same" as another


# -- what was tried -----------------------------------------------------------------------------------


def describe_changes(changes: Changes) -> str:
    """The files that differ between before and after, with the lines added and removed and a look at the new
    ones. Nothing here comes from the model."""
    parts = [_describe(path, before, after) for path, (before, after) in changes.items() if before != after]
    if not parts:
        return "no file was changed"
    shown, rest = parts[:MAX_FILES], len(parts) - MAX_FILES
    return "\n".join([*shown, f"and {rest} more file(s)"] if rest > 0 else shown)


def _describe(path: str, before: str | None, after: str | None) -> str:
    old = [] if before is None else before.splitlines()
    new = [] if after is None else after.splitlines()
    diff = list(difflib.unified_diff(old, new, lineterm="", n=0))
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
    if before is None:
        head = f"created {path} (+{len(added)})"
    elif after is None:
        head = f"deleted {path} (-{len(removed)})"
    else:
        head = f"{path}: +{len(added)} -{len(removed)}"
    shown = [f"    + {_cut(line)}" for line in added[:MAX_NEW_LINES]]
    if len(added) > MAX_NEW_LINES:
        shown.append(f"    (+{len(added) - MAX_NEW_LINES} more line(s))")
    return "\n".join([head, *shown])


# -- what it taught -----------------------------------------------------------------------------------


def make_lesson(attempt: int, changes: Changes, pytest_output: str) -> Lesson:
    """The lesson of an attempt: `pytest_output` must be the output of the tests run AFTER the change."""
    failures = parse_failures(pytest_output)
    if failures is None:
        return Lesson(attempt, describe_changes(changes), NOT_READABLE, None)
    return Lesson(attempt, describe_changes(changes), _names(failures), fingerprint_of(failures))


def _names(failures: list[Failure]) -> str:
    named = "; ".join(f"{f.test}: {f.kind}" for f in failures[:MAX_FAILURES])
    rest = len(failures) - MAX_FAILURES
    return f"{named}; and {rest} more" if rest > 0 else named


# -- what the agent is told ---------------------------------------------------------------------------


def structured_feedback(pytest_output: str, history: list[Lesson], *, latest: Lesson | None = None) -> str:
    """A few lines: which tests failed and how, whether this failure came back, what was already tried.

    `history` is the attempts BEFORE the one that produced `pytest_output`: this failure is compared to them.
    `latest` is the lesson of that attempt itself: it is listed with what was tried, and never compared
    with (it would always be "the same failure" as itself)."""
    tried = [*history, latest] if latest is not None else history
    failures = parse_failures(pytest_output)
    if failures is None:
        tail = [_cut(line, 200) for line in pytest_output.splitlines()[-TAIL_LINES:]]
        lines = [f"The tests were run and I {NOT_READABLE}. The end of the output:", *tail]
        return "\n".join([*lines, *_tried(tried)])
    lines = [f"{len(failures)} test(s) failed:"]
    lines += [f"- {f.test}: {_cut(f.message or f.kind)}" for f in failures[:MAX_FAILURES]]
    if len(failures) > MAX_FAILURES:
        lines.append(f"- and {len(failures) - MAX_FAILURES} more")
    again = [str(lesson.attempt) for lesson in history if lesson.fingerprint == fingerprint_of(failures)]
    if again:
        word = "attempt" if len(again) == 1 else "attempts"
        lines.append(
            f"This is the same failure as in {word} {', '.join(again)}: what was done then did not fix it."
        )
    return "\n".join([*lines, *_tried(tried)])


def _tried(history: list[Lesson]) -> list[str]:
    if not history:
        return []
    lines = ["Already tried, and it did not work:"]
    for lesson in history[-MAX_LESSONS:]:
        tried = _cut(" ".join(lesson.tried.split()), 200)
        lines.append(
            f"- attempt {lesson.attempt}: {tried} -> failed because {_cut(lesson.failed_because, 200)}"
        )
    return lines


def _cut(text: str, limit: int = CUT) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"
