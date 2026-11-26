"""Hooks the harness runs by itself to check the model's work: lint after an edit, and tests before it may
stop (green for a fix run, red for the right reason for a reproduction run).

The model cannot call them, skip them or change what they run. Both run in the sandbox, so nothing the
model wrote ever runs on the host. A check that could not run is never reported as a check that passed.
"""

from pathlib import Path

from prova.agent.repro import REPRO_ARGV, classify_repro
from prova.errors import ToolError
from prova.harness.hooks import HookDecision, HookEvent
from prova.tools.base import RunContext
from prova.tools.paths import resolve_inside
from prova.tools.run_tests import PYTEST_ARGV

# compile() builds the code without running it: a syntax error is an exception, anything else is not.
_COMPILE = "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')"
_CODE_TOOLS = ("edit_file", "create_file")
_EXIT_PASSED, _EXIT_FAILED, _EXIT_INTERRUPTED, _EXIT_NO_TESTS = 0, 1, 2, 5


class LintAfterEdit:
    """After a tool wrote a Python file: does it compile, and does ruff accept it? Information only."""

    name = "lint_after_edit"

    def __init__(self, *, timeout_s: float = 30.0, max_output_chars: int = 2_000) -> None:
        self._timeout_s = timeout_s
        self._max_output_chars = max_output_chars

    def __call__(self, ev: HookEvent) -> HookDecision:
        path = _changed_python_file(ev)
        if path is None:
            return HookDecision.allow()
        sandbox = ev.ctx.sandbox
        if sandbox is None:
            return HookDecision.allow("lint did not run: this run has no sandbox")

        compiled = sandbox.run(["python", "-c", _COMPILE, path], timeout_s=self._timeout_s, cwd="/work")
        if compiled.timed_out:
            return HookDecision.allow(f"lint did not finish within {self._timeout_s:g} seconds ({path})")
        if compiled.exit_code != 0:
            shown = _keep_end(compiled.output, self._max_output_chars)
            return HookDecision.allow(f"{path} does not compile:\n{shown}")

        # --no-cache: otherwise ruff writes a .ruff_cache folder into the workspace. `--` before the path:
        # it is a path, never an option.
        ruff = ["python", "-m", "ruff", "check", "--no-cache", "--output-format=concise", "--", path]
        checked = sandbox.run(ruff, timeout_s=self._timeout_s, cwd="/work")
        if checked.timed_out:
            return HookDecision.allow(f"lint did not finish within {self._timeout_s:g} seconds ({path})")
        if checked.exit_code == 0:
            return HookDecision.allow(f"{path} compiles and passes ruff")
        if checked.exit_code == 1:  # ruff's own code for "I ran and found problems"
            shown = _keep_start(checked.output, self._max_output_chars)
            return HookDecision.allow(f"ruff found problems in {path}:\n{shown}")
        return HookDecision.allow(f"lint did not run: ruff ended with exit code {checked.exit_code}")


class TestsBeforeStop:
    """When the model says it is done, run the tests. Red, or not checkable: it may not end yet."""

    __test__ = False  # the name starts with "Test": tell pytest this is not a test class
    name = "tests_before_stop"

    def __init__(
        self, *, target: str | None = None, timeout_s: float = 120.0, max_output_chars: int = 3_000
    ) -> None:
        self._target = target
        self._timeout_s = timeout_s
        self._max_output_chars = max_output_chars

    def __call__(self, ev: HookEvent) -> HookDecision:
        sandbox = ev.ctx.sandbox
        if sandbox is None:
            return HookDecision.continue_run("the tests cannot be checked: this run has no sandbox")
        argv = [*PYTEST_ARGV, *(["--", self._target] if self._target is not None else [])]
        result = sandbox.run(argv, timeout_s=self._timeout_s, cwd="/work")

        if result.timed_out:
            return HookDecision.continue_run(
                f"the tests could not be checked: they did not finish within {self._timeout_s:g} seconds"
            )
        shown = _keep_end(result.output, self._max_output_chars)  # the end: that is where pytest summarises
        code = result.exit_code
        if code == _EXIT_PASSED:
            return HookDecision.allow("the tests pass")
        if code == _EXIT_NO_TESTS:
            return HookDecision.allow("no tests were collected, so there is nothing to check")
        if code == _EXIT_FAILED:
            return HookDecision.continue_run(f"the tests fail:\n{shown}")
        if code == _EXIT_INTERRUPTED:
            return HookDecision.continue_run(
                f"the tests could not be collected (often an import or syntax error):\n{shown}"
            )
        message = f"the tests could not be checked: pytest ended with exit code {code}:\n{shown}"
        return HookDecision.continue_run(message)


class ReproFailsBeforeStop:
    """The stop hook of a reproduction run: it may end only once its tests fail, for the right reason."""

    name = "repro_fails_before_stop"

    def __init__(self, *, timeout_s: float = 120.0, max_output_chars: int = 3_000) -> None:
        self._timeout_s = timeout_s
        self._max_output_chars = max_output_chars

    def __call__(self, ev: HookEvent) -> HookDecision:
        sandbox = ev.ctx.sandbox
        if sandbox is None:
            return HookDecision.continue_run(
                "the reproduction tests cannot be checked: this run has no sandbox"
            )
        result = sandbox.run(REPRO_ARGV, timeout_s=self._timeout_s, cwd="/work")
        if result.timed_out:
            return HookDecision.continue_run(
                "the reproduction tests could not be checked: "
                f"they did not finish within {self._timeout_s:g} seconds"
            )
        check = classify_repro(
            result.output, exit_code=result.exit_code, truncated=result.truncated, root="/work"
        )
        if check.valid:
            return HookDecision.allow(f"the reproduction tests show the bug: {check.reason}")
        shown = _keep_end(result.output, self._max_output_chars)
        return HookDecision.continue_run(
            f"the reproduction tests do not show the bug yet: {check.reason}\n{shown}"
        )


def _changed_python_file(ev: HookEvent) -> str | None:
    """The workspace-relative path of the Python file this event's tool just wrote, or None."""
    if ev.tool not in _CODE_TOOLS or ev.result is None or not ev.result.success:
        return None
    path = (ev.args or {}).get("path")
    if not isinstance(path, str) or not path.endswith(".py"):
        return None
    return _inside(ev.ctx, path)


def _inside(ctx: RunContext, path: str) -> str | None:
    """`path` relative to the workspace, or None when it is outside or protected (never handed on)."""
    try:
        target = resolve_inside(ctx.workspace, path, deny=ctx.deny)
    except ToolError:
        return None
    return target.relative_to(ctx.workspace.resolve()).as_posix()


def _keep_start(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.rstrip("\n")
    return f"{text[:limit].rstrip(chr(10))}\n[showing the first {limit} of {len(text)} characters]"


def _keep_end(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.rstrip("\n")
    return f"{text[-limit:].lstrip(chr(10))}\n[showing the last {limit} of {len(text)} characters]"


class FilesListed:
    """When the model says it is done: has it handed in a report, and does the report say what really changed?

    What really changed is what the snapshots show. What the agent says it changed is the report. Both ways of
    being wrong are caught: a file changed and not listed, and a file listed that did not change.
    """

    name = "files_listed"

    def __init__(self, *, max_listed: int = 10) -> None:
        self._max_listed = max_listed

    def __call__(self, ev: HookEvent) -> HookDecision:
        report = ev.ctx.submission.report
        if report is None:
            return HookDecision.continue_run(
                "Your report was not handed in. Call submit_result with files_changed_with_reason, "
                "test_command and assumptions, then finish."
            )
        root = ev.ctx.workspace.resolve()
        changed = {_relative(root, path) for path in ev.ctx.snapshots.changed()}
        listed = {change.path for change in report.files_changed_with_reason}
        unlisted, invented = sorted(changed - listed), sorted(listed - changed)
        if not unlisted and not invented:
            return HookDecision.allow()
        lines = ["Your report does not match what changed."]
        if unlisted:
            lines.append(f"- changed but not listed: {self._names(unlisted)}")
        if invented:
            lines.append(f"- listed but not changed: {self._names(invented)}")
        lines.append("Call submit_result again with the right list, then finish.")
        return HookDecision.continue_run("\n".join(lines))

    def _names(self, paths: list[str]) -> str:
        shown = ", ".join(paths[: self._max_listed])
        rest = len(paths) - self._max_listed
        return f"{shown}, and {rest} more" if rest > 0 else shown


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # outside the workspace: shown as it is, so it can never match the report
        return str(path)
