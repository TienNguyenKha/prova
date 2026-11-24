"""M6/M7: the agent fixes a bug in a copy of the t001 fixture and runs the tests in a Docker sandbox.

Run by hand (a few model calls, more than the read-only M4 run because it edits):
  uv run python experiments/act2/m6_fix_t001.py                 # main model
  uv run python experiments/act2/m6_fix_t001.py --model cheap

The agent has the run_tests tool, which runs pytest in the sandbox (no network, only the workspace mounted).
After the agent is done, this script runs the tests itself in the same kind of sandbox: it does not take the
agent's word for it. Needs Docker and the image:  docker build -t prova-sandbox:1 docker/sandbox
If the run does not end well (tests red, tests changed, or the agent did not finish), it is rolled back.
"""

import argparse
import difflib
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from prova.agent.loop import AgentResult, run_agent
from prova.app import App, build_app
from prova.config import Settings, load_settings
from prova.errors import SandboxUnavailableError
from prova.harness.permissions import Profile, get_profile, load_profiles
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.sandbox.base import Sandbox
from prova.sandbox.docker import DockerSandbox
from prova.tools.base import RunContext

FIXTURE = Path("experiments/act2/fixtures/t001")
SOURCE_FILE = "injection_guard.py"
TEST_FILE = "test_injection_guard.py"
TASK = (
    "The function `looks_like_injection` in injection_guard.py misses some attempts to override an "
    "assistant's instructions, for example 'Ignore all previous instructions'. Fix the bug so that every "
    "test in test_injection_guard.py passes. Do not change the tests. You can run them with the "
    "run_tests tool."
)
SYSTEM = (
    "You fix bugs in the code in the workspace. Read the files before you change anything. Change code "
    "with edit_file: old_str must match the file exactly once, and the line numbers that read_file shows "
    "are not part of the file. If an edit fails, read the error: it says what to do. When you are done, "
    "say what you changed."
)
COUNTED_TOOLS = ("edit_file", "create_file", "run_tests")


class UtcClock:
    def __call__(self) -> datetime:
        return datetime.now(UTC)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_tests(sandbox: Sandbox) -> tuple[bool, str]:
    """Run the fixture's tests in the sandbox. Returns (passed, the last lines of the output)."""
    result = sandbox.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], timeout_s=60)
    tail = "\n".join(result.output.strip().splitlines()[-6:])
    return (result.exit_code == 0 and not result.timed_out), tail


def edit_outcomes(log_path: Path) -> Counter[str]:
    """Count how each call of a tool that changes or runs things ended, from the trajectory."""
    outcomes: Counter[str] = Counter()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["type"] == "tool_result" and event["tool"] in COUNTED_TOOLS:
            # run_tests succeeds as a tool even when the tests fail, so say what the tests did instead
            is_tests = event["tool"] == "run_tests"
            what = event["message"].split(" (")[0] if is_tests else event["error_code"] or "ok"
            outcomes[f"{event['tool']}: {what}"] += 1
    return outcomes


def show_diff(before: str, after: str) -> None:
    names = (f"{SOURCE_FILE} (before)", f"{SOURCE_FILE} (after)")
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), *names, lineterm="")
    print("\n".join(diff) or "(the file is unchanged)")


def run_one(  # noqa: PLR0913 - a script: everything it needs is passed in, nothing is global
    args: argparse.Namespace,
    target: Settings,
    model: str,
    app: App,
    profile: Profile,
    workspace: Path,
    sandbox: Sandbox,
    log_path: Path,
    writer: TrajectoryWriter,
    redactor: Redactor,
) -> None:
    source, tests = workspace / SOURCE_FILE, workspace / TEST_FILE
    source_before, tests_hash = source.read_text(encoding="utf-8"), sha256(tests)

    before_ok, _ = run_tests(sandbox)
    print(f"model={model} profile=fix")
    print(f"before the agent: tests {'pass' if before_ok else 'FAIL'} (expected: FAIL)")

    ctx = RunContext.from_profile(profile, workspace, strict=True, sandbox=sandbox)
    result = run_agent(
        TASK,
        llm=app.llm,
        tools=app.tools,
        ctx=ctx,
        settings=target,
        system=SYSTEM,
        max_iterations=args.max_iterations,
        trajectory=writer,
        redactor=redactor,
    )

    source_after = source.read_text(encoding="utf-8")
    tests_changed = sha256(tests) != tests_hash
    passed, tail = run_tests(sandbox)  # our own run: the agent's word is not enough

    report(result, log_path, source_before, source_after, tests_changed, passed, tail)

    if result.status == "done" and passed and not tests_changed:
        ctx.snapshots.discard()
        print("\nkept the change.")
    else:
        ctx.snapshots.rollback_all()
        restored = source.read_text(encoding="utf-8") == source_before and sha256(tests) == tests_hash
        print(f"\nrolled back. files are back to the original: {restored}")


def main() -> None:
    parser = argparse.ArgumentParser(description="M6: let the agent fix the t001 fixture")
    parser.add_argument("--model", choices=["cheap", "main"], default="main")
    parser.add_argument("--max-iterations", type=int, default=20)
    args = parser.parse_args()

    settings = load_settings()
    model = settings.cheap_model if args.model == "cheap" else settings.model
    target = settings.model_copy(update={"model": model})
    app = build_app(target)
    profile = get_profile(load_profiles(settings.project_root / "permissions.yaml"), "fix")

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    log_path = settings.project_root / "experiments" / "runs" / f"m6-{run_id}.jsonl"
    redactor = Redactor.from_environ(os.environ)
    writer = TrajectoryWriter(log_path, run_id=run_id, clock=UtcClock(), redactor=redactor)

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        skip = shutil.ignore_patterns("__pycache__")
        shutil.copytree(settings.project_root / FIXTURE, workspace, dirs_exist_ok=True, ignore=skip)
        try:
            with DockerSandbox(workspace) as sandbox:
                run_one(
                    args, target, model, app, profile, workspace, sandbox, log_path, writer, redactor
                )
        except SandboxUnavailableError as e:
            print(f"cannot run: {e}")
            raise SystemExit(1) from None


def report(
    result: AgentResult,
    log_path: Path,
    before: str,
    after: str,
    tests_changed: bool,
    passed: bool,
    tail: str,
) -> None:
    print(f"\n=== agent: status={result.status} iterations={result.iterations} detail={result.detail!r}")
    print(f"tokens: input={result.usage.total_input_tokens} output={result.usage.total_output_tokens}")
    print(f"trajectory: {log_path}")
    print("\n=== what the agent changed")
    show_diff(before, after)
    print(f"\n=== tests changed by the agent: {tests_changed}")
    print(f"=== tests after the agent: {'PASS' if passed else 'FAIL'}\n{tail}")
    print("\n=== how the calls to the writing tools ended")
    outcomes = edit_outcomes(log_path)
    for name, count in sorted(outcomes.items()):
        print(f"  {count} x {name}")
    if not outcomes:
        print("  (the agent never called a writing tool)")
    print(f"\n=== final answer\n{result.final_text}")


if __name__ == "__main__":
    main()
