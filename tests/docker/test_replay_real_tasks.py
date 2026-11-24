"""Replay of the recorded run on the three real tasks, with the real harness and a real container.

The model is not asked: its answers come from bench/cassettes/dev.jsonl. Everything else is real (the tools,
the hooks, pytest in the sandbox, the grading), so if a change to the harness makes the agent act differently
on the same answers, the chain of tool calls is no longer the recorded one and these tests say so. A request
that was not recorded is a CassetteMiss, never a call to the API.

To record again (about $0.20, needs the API):
  uv run python -m bench.run --record bench/cassettes/dev.jsonl --confirm   (after moving the old file away)
  uv run pytest -m docker -q
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bench.product import make_agent
from bench.run import select_tasks
from bench.runner import run_task
from prova.agent.budget import Pricing
from prova.app import build_app
from prova.config import Settings
from prova.harness.permissions import load_profiles
from prova.llm.cassette import ReplayClient
from prova.sandbox.docker import DockerSandbox

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[2]
CASSETTE = ROOT / "bench" / "cassettes" / "dev.jsonl"
# What the recorded run ended with. The grading is run again here, in a real container.
RESOLVED = {
    "t001_injection_regex": False,  # its regex also flags a negated phrase (see LEARNINGS)
    "t002_url_branch_unreachable": True,
    "t006_fail_open_classifier": True,
}


class Replayed:
    def __init__(self, rows: list[dict[str, Any]], replay: ReplayClient, results: Path) -> None:
        self.rows, self.replay, self.results = rows, replay, results


@pytest.fixture(scope="module")
def replayed(tmp_path_factory: pytest.TempPathFactory) -> Replayed:
    settings = Settings(anthropic_api_key="sk-ant-replay", project_root=ROOT)  # type: ignore[arg-type]
    replay = ReplayClient(CASSETTE)
    agent = make_agent(
        app=build_app(settings, llm=replay),
        profiles=load_profiles(ROOT / "permissions.yaml"),
        root=ROOT,
        pricing=Pricing(input_per_mtok=2.0, output_per_mtok=10.0),
        open_sandbox=DockerSandbox,
        clock=lambda: datetime.now(UTC),
    )
    results = tmp_path_factory.mktemp("replay") / "results.jsonl"
    rows = [
        run_task(
            folder,
            agent=agent,
            open_sandbox=DockerSandbox,
            pricing=Pricing(2.0, 10.0),
            results=results,
            run_id="replay",
            seed=0,
            config=agent.config,
        )  # fmt: skip
        for folder in select_tasks(ROOT / "bench" / "tasks", "dev", [])
    ]
    return Replayed(rows, replay, results)


def recorded_chain() -> list[tuple[str, dict[str, Any]]]:
    """Every tool call the model asked for in the cassette, in the order it asked."""
    chain: list[tuple[str, dict[str, Any]]] = []
    for line in CASSETTE.read_text(encoding="utf-8").splitlines():
        for block in json.loads(line)["response"]["content"]:
            if block["type"] == "tool_use":
                chain.append((block["name"], block["input"]))
    return chain


def replayed_chain(replayed: Replayed) -> list[tuple[str, dict[str, Any]]]:
    chain: list[tuple[str, dict[str, Any]]] = []
    for row in replayed.rows:
        log = replayed.results.parent / row["trajectory"]
        for line in log.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event["type"] == "tool_call":
                chain.append((event["tool"], event["input"]))
    return chain


def test_every_run_ends_normally_and_nothing_asked_for_was_missing(replayed: Replayed) -> None:
    assert [(r["task_id"], r["agent_status"], r["error"]) for r in replayed.rows] == [
        (task, "done", None) for task in RESOLVED
    ]


def test_the_chain_of_tool_calls_is_the_recorded_one(replayed: Replayed) -> None:
    assert replayed_chain(replayed) == recorded_chain()


def test_every_recorded_answer_was_used(replayed: Replayed) -> None:
    assert replayed.replay.unused == 0


def test_the_grading_gives_what_the_recorded_run_got(replayed: Replayed) -> None:
    assert {r["task_id"]: r["resolved"] for r in replayed.rows} == RESOLVED
