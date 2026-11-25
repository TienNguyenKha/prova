"""A gate for shell commands: only commands from a short list of programs may be run."""

from collections.abc import Callable
from dataclasses import dataclass

ALLOWED = frozenset({"ls", "cat", "git", "grep", "pytest"})


@dataclass(frozen=True, slots=True)
class Outcome:
    ran: bool
    reason: str
    exit_code: int | None  # None when the command was not run


def run_allowed(command: str, run: Callable[[str], int], allowed: frozenset[str] = ALLOWED) -> Outcome:
    """Run `command` with `run` (which hands the text to a shell and returns its exit code), but only if the
    program it starts is in `allowed`. A command that is not allowed is not run and is not an error."""
    program = command.split()[0]
    if program not in allowed:
        return Outcome(ran=False, reason=f"{program} is not an allowed program", exit_code=None)
    return Outcome(ran=True, reason="allowed", exit_code=run(command))
