"""What a sandbox is, as far as the rest of Prova is concerned: something that runs a command and reports.

Nothing outside this package should know whether the backend is Docker or something else.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExecResult:
    exit_code: int  # meaningless when `timed_out` is true
    stdout: str
    stderr: str
    timed_out: bool  # the command was stopped because it ran out of time
    duration_s: float
    truncated: bool  # the sandbox kept only the first part of the output

    @property
    def output(self) -> str:
        """stdout then stderr, as one text."""
        if self.stdout and self.stderr and not self.stdout.endswith("\n"):
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout + self.stderr


class Sandbox(Protocol):
    """Runs commands somewhere that cannot hurt the machine. A command is a list of arguments, never a string
    for a shell. Raises SandboxUnavailableError when it cannot run at all: it never falls back to the host."""

    def run(
        self,
        argv: list[str],
        *,
        timeout_s: float,
        cwd: str = "/work",
        on_output: Callable[[str], None] | None = None,
    ) -> ExecResult: ...

    def close(self) -> None: ...
