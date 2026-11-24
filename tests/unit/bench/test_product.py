"""The agent of the product, as the benchmark runs it: the same profile, hooks, prompt and contract.

A fake model and a fake sandbox stand in for the API and Docker, so nothing here costs money. What is checked
is the wiring: that the contract is checked before a run starts, that the budget of the task is the one the
loop enforces, that the issue reaches the model as data, and that the prompt hash in the trajectory is the
one in the config of the result.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bench.product import make_agent
from bench.task import Task, load_task
from prova.agent.budget import Pricing
from prova.app import build_app
from prova.config import Settings
from prova.errors import ContractError
from prova.harness.permissions import Permission, Profile, load_profiles
from prova.sandbox.base import Sandbox
from tests.fakes import BuggyTool, FakeLLM, FakeSandbox, text_message, tool_use_message
from tests.unit.bench.test_task import make_task

ROOT = Path(__file__).resolve().parents[3]
PRICING = Pricing(input_per_mtok=2.0, output_per_mtok=10.0)


def utc() -> datetime:
    return datetime.now(UTC)


class Rig:
    def __init__(
        self, tmp_path: Path, settings: Settings, replies: list[Any], budget: dict[str, Any], tools: list[Any]
    ) -> None:
        self.tmp = tmp_path
        self.task: Task = load_task(make_task(tmp_path / "tasks", {"budget": budget}))
        self.llm = FakeLLM(replies)
        self.app = build_app(settings, llm=self.llm, extra_tools=tools)
        self.profiles = load_profiles(ROOT / "permissions.yaml")
        self.sandboxes: list[Path] = []
        self.closed = 0
        self.workspace = tmp_path / "agent"
        self.workspace.mkdir()
        self.trajectory = tmp_path / "out" / "trajectories" / "run1-t001-s0.jsonl"

    @contextmanager
    def open_sandbox(self, workspace: Path) -> Iterator[Sandbox]:
        self.sandboxes.append(workspace)
        try:
            yield FakeSandbox()
        finally:
            self.closed += 1

    def agent(self, profiles: dict[str, Profile] | None = None) -> Any:
        return make_agent(
            app=self.app, profiles=profiles or self.profiles, root=ROOT, pricing=PRICING,
            open_sandbox=self.open_sandbox, clock=utc,
        )  # fmt: skip

    def events(self) -> list[dict[str, Any]]:
        lines = self.trajectory.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]


def rig(
    tmp_path: Path,
    settings: Settings,
    replies: list[Any] | None = None,
    tools: list[Any] | None = None,
    **budget: Any,
) -> Rig:
    limits = {"max_tool_calls": 40, "max_usd": 0.5, "max_seconds": 600, **budget}
    done = [text_message("done")] * 3  # the stop hooks say "not yet" twice before they give up
    return Rig(tmp_path, settings, replies or done, limits, tools or [])


# --- what the config says ---------------------------------------------------------------------------


def test_the_config_says_what_the_run_used(tmp_path: Path, settings: Settings) -> None:
    config = rig(tmp_path, settings).agent().config
    assert config["model"] == settings.model and config["profile"] == "fix"
    assert config["hooks"] == ["lint_after_edit", "files_listed", "tests_before_stop"]
    assert len(config["prompt_hash"]) == 64


def test_a_contract_that_does_not_hold_stops_everything_before_a_run(
    tmp_path: Path, settings: Settings
) -> None:
    r = rig(tmp_path, settings)
    loose = {**r.profiles, "fix": Profile(allow=frozenset(Permission), write_paths=("**",))}  # has network
    with pytest.raises(ContractError):
        r.agent(loose)
    assert r.sandboxes == []


# --- what a run does --------------------------------------------------------------------------------


def test_the_issue_reaches_the_model_as_data_and_the_tools_are_those_of_the_profile(
    tmp_path: Path, settings: Settings
) -> None:
    r = rig(tmp_path, settings)
    r.agent()(r.task, r.workspace, r.trajectory)
    call = r.llm.messages.calls[0]
    first = call.messages[0]["content"]
    assert isinstance(first, str) and first.startswith("<untrusted_issue>") and r.task.issue in first
    assert call.system
    offered = {t["name"] for t in call.tools}
    assert offered == {t["name"] for t in r.app.tools.to_api_tools(r.profiles["fix"].allow)}


def test_the_sandbox_is_opened_on_the_workspace_and_closed_after(tmp_path: Path, settings: Settings) -> None:
    r = rig(tmp_path, settings)
    r.agent()(r.task, r.workspace, r.trajectory)
    assert r.sandboxes == [r.workspace] and r.closed == 1


def test_the_sandbox_is_closed_even_when_the_run_fails(tmp_path: Path, settings: Settings) -> None:
    r = rig(tmp_path, settings, [RuntimeError("the API went away")])
    with pytest.raises(RuntimeError):
        r.agent()(r.task, r.workspace, r.trajectory)
    assert r.closed == 1


def test_the_trajectory_starts_with_the_prompt_hash_of_the_config_and_ends_with_an_outcome(
    tmp_path: Path, settings: Settings
) -> None:
    r = rig(tmp_path, settings)
    agent = r.agent()
    agent(r.task, r.workspace, r.trajectory)
    events = r.events()
    assert events[0]["type"] == "task" and events[0]["prompt_hash"] == agent.config["prompt_hash"]
    assert events[-1]["type"] == "outcome" and events[0]["run_id"] == "run1-t001-s0"


# --- the budget of the task -------------------------------------------------------------------------


def test_the_tool_call_limit_of_the_task_is_enforced_before_the_calls_run(
    tmp_path: Path, settings: Settings
) -> None:
    two = tool_use_message(("a", "list_dir", {"path": "."}), ("b", "list_dir", {"path": "."}))
    r = rig(tmp_path, settings, [two], max_tool_calls=1)
    run = r.agent()(r.task, r.workspace, r.trajectory)
    assert run.status == "budget"
    assert not [e for e in r.events() if e["type"] == "tool_call"]


def test_the_money_limit_of_the_task_is_priced_with_the_table_and_ends_the_run(
    tmp_path: Path, settings: Settings
) -> None:
    spends = tool_use_message(("a", "list_dir", {"path": "."}), usage=(1000, 500))  # $0.007
    r = rig(tmp_path, settings, [spends, text_message("never asked")], max_usd=0.001)
    run = r.agent()(r.task, r.workspace, r.trajectory)
    assert run.status == "budget" and len(r.llm.messages.calls) == 1
    assert run.usage.total_input_tokens == 1000


# --- what the product does that a benchmark must not hide -------------------------------------------


def test_the_stop_hooks_are_in_the_run_and_keep_it_going_when_nothing_was_checked(
    tmp_path: Path, settings: Settings
) -> None:
    r = rig(tmp_path, settings)  # the model says "done" three times; no tests were run, no report handed in
    r.agent()(r.task, r.workspace, r.trajectory)
    assert len(r.llm.messages.calls) == 3
    assert any(e["type"] == "hook_decision" and e["action"] == "continue" for e in r.events())


def test_a_bug_in_a_tool_stops_the_run_and_is_not_hidden_from_the_result(
    tmp_path: Path, settings: Settings
) -> None:
    calls = tool_use_message(("a", "buggy", {"text": "x"}))
    r = rig(tmp_path, settings, [calls], tools=[BuggyTool()])
    with pytest.raises(RuntimeError, match="secret-detail"):
        r.agent()(r.task, r.workspace, r.trajectory)
