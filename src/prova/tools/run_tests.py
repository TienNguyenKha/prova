"""run_tests: run the project's tests inside the sandbox. The model cannot change the command."""

from pydantic import BaseModel, ConfigDict, Field

from prova.errors import ErrorCode, SandboxUnavailableError, ToolError
from prova.tools.base import Permission, RunContext, ToolResult
from prova.tools.paths import resolve_inside

DEFAULT_TIMEOUT_S = 120.0
DEFAULT_OUTPUT_CHARS = 6_000  # how much output the model sees: the END, where pytest prints its summary
_PYTEST = ["python", "-m", "pytest", "-x", "-q"]

# pytest's exit codes that mean it ran and has something to say
_EXIT_PASSED, _EXIT_FAILED, _EXIT_INTERRUPTED, _EXIT_NO_TESTS = 0, 1, 2, 5


class RunTestsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str | None = Field(
        default=None,
        description="A test file or directory, relative to the workspace root. Omit it to run all the tests",
    )


class RunTestsTool:
    name = "run_tests"
    description = (
        "Run the tests of the project in an isolated sandbox with no network, and stop at the first failure. "
        "You may name a test file or directory; you cannot pass other options."
    )
    args_model = RunTestsArgs
    permission = Permission.EXEC

    def __init__(
        self, *, timeout_s: float = DEFAULT_TIMEOUT_S, max_output_chars: int = DEFAULT_OUTPUT_CHARS
    ) -> None:
        self._timeout_s = timeout_s
        self._max_output_chars = max_output_chars

    def run(self, args: RunTestsArgs, ctx: RunContext) -> ToolResult:
        sandbox = ctx.sandbox
        if sandbox is None:
            message = "this run has no sandbox, and commands are never run outside one: nothing was run"
            raise ToolError(ErrorCode.SAFETY, message)

        argv = list(_PYTEST)
        if args.target is not None:
            target = resolve_inside(ctx.workspace, args.target, deny=ctx.deny)
            if not target.exists():
                raise ToolError(ErrorCode.NOT_FOUND, f"no such file or directory: {args.target}")
            # After `--` whatever follows is a path for pytest, never an option, even with a leading dash.
            argv += ["--", target.relative_to(ctx.workspace.resolve()).as_posix()]

        try:
            result = sandbox.run(argv, timeout_s=self._timeout_s, cwd="/work")
        except SandboxUnavailableError as e:
            message = f"the sandbox is not available: {e}. Nothing was run."
            raise ToolError(ErrorCode.TRANSIENT, message) from None

        shown, notes = self._shown(result.output, truncated=result.truncated)
        if result.timed_out:
            message = f"the tests did not finish within {self._timeout_s:g} seconds: they were stopped"
            raise ToolError(ErrorCode.TIMEOUT, f"{message}. What they printed so far:\n{shown}")

        took = f"exit code {result.exit_code}, {result.duration_s:.1f}s"
        code = result.exit_code
        if code == _EXIT_PASSED:
            summary = f"tests passed ({took})"
        elif code == _EXIT_FAILED:
            summary = f"tests FAILED ({took})"
        elif code == _EXIT_NO_TESTS:
            summary = f"no tests were collected ({took})"
        elif code == _EXIT_INTERRUPTED:
            summary = f"pytest was interrupted ({took}): often an import or syntax error in the code"
        else:
            message = f"pytest ended with exit code {code}, which is not a normal pass or fail"
            raise ToolError(ErrorCode.INTERNAL, f"{message}. Output:\n{shown}")

        data = {"exit_code": code, "passed": code == _EXIT_PASSED, "duration_s": result.duration_s}
        snapshot = "\n".join([part for part in (shown, *notes) if part]) or None
        return ToolResult.ok(summary, data=data, snapshot=snapshot)

    def _shown(self, output: str, *, truncated: bool) -> tuple[str, list[str]]:
        """The end of the output, plus a note for every cut: by us, and by the sandbox."""
        notes: list[str] = []
        text = output
        if len(text) > self._max_output_chars:
            notes.append(f"[showing the last {self._max_output_chars} of {len(text)} characters]")
            text = text[-self._max_output_chars :]
        if truncated:
            notes.append("[the sandbox cut the output: it was longer than the limit the sandbox keeps]")
        return text, notes
