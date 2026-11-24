"""Tests for prova.mechanisms: what stands behind each rule of the contract, and how it proves itself.

Every check here is a probe on the real configuration. And each one has a twin that weakens the configuration
on purpose, to show that the probe can fail: a check that cannot fail proves nothing.
"""

from pathlib import Path

import pytest
from pydantic import BaseModel

from prova.contract import VerifyContext
from prova.harness.hooks import Hooks
from prova.harness.permissions import Permission, Profile, load_profiles
from prova.harness.redact import Redactor
from prova.harness.report import Report
from prova.harness.verifiers import FilesListed, LintAfterEdit, TestsBeforeStop
from prova.mechanisms import builtin_mechanisms
from prova.sandbox.docker import DockerSandbox
from prova.tools.base import RunContext, ToolResult
from prova.tools.registry import ToolRegistry
from prova.tools.report import SubmitResultTool
from tests.fakes import make_tool_registry

ROOT = Path(__file__).resolve().parents[2]


def context(
    tmp_path: Path,
    *,
    hooks: Hooks | None = None,
    extra: dict[str, Profile] | None = None,
    tools: ToolRegistry | None = None,
) -> VerifyContext:
    profiles = {**load_profiles(ROOT / "permissions.yaml"), **(extra or {})}
    real_hooks = Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), TestsBeforeStop()))
    return VerifyContext(
        profiles=profiles,
        hooks=real_hooks if hooks is None else hooks,
        tools=tools if tools is not None else make_tool_registry(SubmitResultTool()),
        project_root=tmp_path,
    )


def run(name: str, ctx: VerifyContext, **params: str) -> str | None:
    return builtin_mechanisms()[name](ctx, params)


def test_the_mechanisms_are_a_fresh_table_every_time() -> None:
    first = builtin_mechanisms()
    first.clear()
    assert "sandbox.network_none" in builtin_mechanisms()


def test_the_names_the_contract_may_use() -> None:
    assert set(builtin_mechanisms()) >= {
        "permissions.write_blocked",
        "permissions.read_blocked",
        "permissions.lacks",
        "sandbox.network_none",
        "trajectory.redaction",
        "hook.stop.tests_before_stop",
        "hook.post.lint_after_edit",
        "hook.stop.files_listed",
        "tool.submit_result",
    }


# --- Writing is blocked where the rule says ------------------------------------------------------------


@pytest.mark.parametrize("path", ["tests/unit/x.py", "pkg/test_y.py", "conftest.py"])
def test_the_fix_profile_blocks_writing_a_test_file(tmp_path: Path, path: str) -> None:
    assert run("permissions.write_blocked", context(tmp_path), profile="fix", path=path) is None


def test_a_profile_that_lets_a_test_file_be_written_is_caught(tmp_path: Path) -> None:
    careless = Profile(allow=frozenset({Permission.READ, Permission.WRITE}), write_paths=("**",))
    ctx = context(tmp_path, extra={"careless": careless})
    problem = run("permissions.write_blocked", ctx, profile="careless", path="tests/unit/x.py")
    assert problem is not None and "careless" in problem and "tests/unit/x.py" in problem


def test_a_profile_with_no_write_permission_blocks_every_write(tmp_path: Path) -> None:
    assert (
        run("permissions.write_blocked", context(tmp_path), profile="review", path="src/anything.py") is None
    )


def test_the_probe_does_not_leave_anything_in_the_project(tmp_path: Path) -> None:
    run("permissions.write_blocked", context(tmp_path), profile="fix", path="src/app.py")  # this one succeeds
    assert list(tmp_path.iterdir()) == []  # the probe wrote in a folder of its own


@pytest.mark.parametrize(
    ("params", "mentioned"),
    [
        ({"path": "tests/x.py"}, "profile"),
        ({"profile": "fix"}, "path"),
        ({"profile": "nope", "path": "a"}, "nope"),
    ],
)
def test_missing_or_unknown_parameters_are_reported_by_name(
    tmp_path: Path, params: dict[str, str], mentioned: str
) -> None:
    problem = run("permissions.write_blocked", context(tmp_path), **params)
    assert problem is not None and mentioned in problem


# --- Reading is blocked where the rule says ------------------------------------------------------------


@pytest.mark.parametrize("path", [".env", "deploy/id_rsa", "certs/server.pem"])
def test_every_profile_blocks_reading_secrets(tmp_path: Path, path: str) -> None:
    for profile in ("fix", "review"):
        assert run("permissions.read_blocked", context(tmp_path), profile=profile, path=path) is None


def test_a_readable_path_is_reported_when_the_rule_says_it_must_not_be(tmp_path: Path) -> None:
    problem = run("permissions.read_blocked", context(tmp_path), profile="fix", path="README.md")
    assert problem is not None and "README.md" in problem


# --- A profile that lacks a permission --------------------------------------------------------------------


def test_the_fix_profile_has_no_network_permission(tmp_path: Path) -> None:
    assert run("permissions.lacks", context(tmp_path), profile="fix", permission="network") is None


def test_a_profile_that_has_the_permission_is_caught(tmp_path: Path) -> None:
    online = Profile(allow=frozenset({Permission.READ, Permission.NETWORK}))
    problem = run(
        "permissions.lacks",
        context(tmp_path, extra={"online": online}),
        profile="online",
        permission="network",
    )
    assert problem is not None and "online" in problem and "network" in problem


def test_a_permission_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    problem = run("permissions.lacks", context(tmp_path), profile="fix", permission="teleport")
    assert problem is not None and "teleport" in problem


# --- The sandbox has no network ----------------------------------------------------------------------------


def test_the_sandbox_is_started_without_a_network(tmp_path: Path) -> None:
    assert run("sandbox.network_none", context(tmp_path)) is None


def test_a_sandbox_command_that_lost_its_network_flag_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = DockerSandbox.build_command

    def without_the_flag(self: DockerSandbox, name: str, argv: list[str], cwd: str) -> list[str]:
        command = original(self, name, argv, cwd)
        at = command.index("--network")
        return command[:at] + command[at + 2 :]

    monkeypatch.setattr(DockerSandbox, "build_command", without_the_flag)
    problem = run("sandbox.network_none", context(tmp_path))
    assert problem is not None and "--network none" in problem


# --- Secrets are removed from the log ---------------------------------------------------------------------


def test_the_redactor_removes_a_key_and_a_private_key(tmp_path: Path) -> None:
    assert run("trajectory.redaction", context(tmp_path)) is None


def test_a_redactor_that_removes_nothing_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Redactor, "redact", lambda self, text: text)
    problem = run("trajectory.redaction", context(tmp_path))
    assert problem is not None and "redact" in problem


# --- Hooks that are really installed -----------------------------------------------------------------------


def test_the_hooks_of_a_real_run_are_found_where_they_belong(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    assert run("hook.stop.tests_before_stop", ctx) is None
    assert run("hook.post.lint_after_edit", ctx) is None


def test_a_hook_that_is_not_installed_is_caught(tmp_path: Path) -> None:
    ctx = context(tmp_path, hooks=Hooks())
    problem = run("hook.stop.tests_before_stop", ctx)
    assert problem is not None and "tests_before_stop" in problem and "not installed" in problem


def test_a_hook_in_the_wrong_list_does_not_count(tmp_path: Path) -> None:
    ctx = context(tmp_path, hooks=Hooks(stop=(LintAfterEdit(),)))  # a lint hook, but asked at stop
    assert run("hook.post.lint_after_edit", ctx) is not None


# --- The report-check hook is installed ------------------------------------------------------------------


def test_the_report_check_is_found_among_the_stop_hooks(tmp_path: Path) -> None:
    assert run("hook.stop.files_listed", context(tmp_path)) is None


def test_a_run_without_the_report_check_is_caught(tmp_path: Path) -> None:
    ctx = context(tmp_path, hooks=Hooks(stop=(TestsBeforeStop(),)))
    problem = run("hook.stop.files_listed", ctx)
    assert problem is not None and "files_listed" in problem and "not installed" in problem


# --- The agent can hand in its report ----------------------------------------------------------------------


class _Args(BaseModel):
    note: str = ""


class ForgetfulSubmit:
    """Looks like submit_result, takes anything, and records nothing."""

    name = "submit_result"
    description = "Pretends to take a report."
    args_model = Report
    permission = Permission.READ

    def run(self, args: Report, ctx: RunContext) -> ToolResult:
        return ToolResult.ok("thanks")


class WrongFieldsSubmit:
    name = "submit_result"
    description = "Takes the wrong fields."
    args_model = _Args
    permission = Permission.READ

    def run(self, args: _Args, ctx: RunContext) -> ToolResult:
        return ToolResult.ok("thanks")


class WriteOnlySubmit(SubmitResultTool):
    permission = Permission.WRITE  # a review run cannot call it


def test_the_agent_can_hand_in_a_report_with_the_real_tool(tmp_path: Path) -> None:
    assert run("tool.submit_result", context(tmp_path), profile="fix") is None
    assert run("tool.submit_result", context(tmp_path)) is None  # and without naming a profile


def test_the_probe_does_not_leave_a_report_behind_in_the_project(tmp_path: Path) -> None:
    run("tool.submit_result", context(tmp_path), profile="fix")
    assert list(tmp_path.iterdir()) == []


def test_a_run_whose_registry_has_no_submit_tool_is_caught(tmp_path: Path) -> None:
    problem = run("tool.submit_result", context(tmp_path, tools=make_tool_registry()))
    assert problem is not None and "submit_result" in problem and "not registered" in problem


def test_a_tool_with_the_wrong_fields_is_caught_and_the_missing_ones_are_named(tmp_path: Path) -> None:
    problem = run("tool.submit_result", context(tmp_path, tools=make_tool_registry(WrongFieldsSubmit())))
    assert problem is not None and "test_command" in problem and "files_changed_with_reason" in problem


def test_a_tool_that_takes_the_report_and_does_not_record_it_is_caught(tmp_path: Path) -> None:
    problem = run("tool.submit_result", context(tmp_path, tools=make_tool_registry(ForgetfulSubmit())))
    assert problem is not None and "did not record" in problem


def test_a_submit_tool_that_a_profile_is_not_offered_is_caught(tmp_path: Path) -> None:
    problem = run(
        "tool.submit_result", context(tmp_path, tools=make_tool_registry(WriteOnlySubmit())), profile="review"
    )
    assert problem is not None and "review" in problem and "not offered" in problem


def test_an_unknown_profile_is_reported_by_name(tmp_path: Path) -> None:
    problem = run("tool.submit_result", context(tmp_path), profile="nope")
    assert problem is not None and "nope" in problem
