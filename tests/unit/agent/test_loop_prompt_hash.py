"""A run writes down which prompt it ran with, as the hash of what the model was told.

So that two runs of the same task can be told apart by their prompt, and a result can be tied to the exact
words and tool definitions that produced it.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from prova.agent.loop import run_agent
from prova.app import build_app
from prova.config import Settings
from prova.harness.permissions import load_profiles
from prova.harness.redact import Redactor
from prova.harness.trajectory import SCHEMA_VERSION, TrajectoryWriter
from prova.prompts.render import render_system, render_user_context
from prova.tools.base import RunContext
from tests.fakes import FakeLLM, make_tool_registry, text_message
from tests.unit.agent.test_loop_trajectory import DatetimeClock

ROOT = Path(__file__).resolve().parents[3]
A_HASH = "a" * 64


def go(
    settings: Settings, tmp_path: Path, *, prompt_hash: str | None, llm: FakeLLM | None = None
) -> list[dict[str, Any]]:
    log = tmp_path / "trajectory.jsonl"
    writer = TrajectoryWriter(log, run_id="r", clock=DatetimeClock(), redactor=Redactor())
    run_agent(
        "the task",
        llm=llm or FakeLLM([text_message("done")]),
        tools=make_tool_registry(),
        ctx=RunContext(workspace=tmp_path),
        settings=settings,
        system="You are careful.",
        trajectory=writer,
        prompt_hash=prompt_hash,
    )
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_the_hash_of_the_prompt_is_in_the_first_line_of_the_log(settings: Settings, tmp_path: Path) -> None:
    records = go(settings, tmp_path, prompt_hash=A_HASH)
    assert records[0]["type"] == "task" and records[0]["prompt_hash"] == A_HASH


def test_a_run_that_does_not_say_which_prompt_it_used_has_a_null_hash(
    settings: Settings, tmp_path: Path
) -> None:
    assert go(settings, tmp_path, prompt_hash=None)[0]["prompt_hash"] is None


def test_adding_the_field_does_not_change_the_format_version(settings: Settings, tmp_path: Path) -> None:
    records = go(settings, tmp_path, prompt_hash=A_HASH)
    assert {r["schema_version"] for r in records} == {SCHEMA_VERSION} == {1}


@pytest.mark.parametrize(
    "bad", ["", "not a hash", "A" * 64, "a" * 63, "a" * 65, "You are Prova, a security coding agent"]
)
def test_something_that_is_not_a_sha256_is_refused_before_the_model_is_called(
    settings: Settings, tmp_path: Path, bad: str
) -> None:
    llm = FakeLLM([text_message("done")])
    with pytest.raises(ValidationError):
        go(settings, tmp_path, prompt_hash=bad, llm=llm)
    assert llm.messages.calls == []


def test_the_hash_is_checked_even_when_nobody_is_writing_a_log(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([text_message("done")])
    with pytest.raises(ValidationError):
        run_agent(
            "t",
            llm=llm,
            tools=make_tool_registry(),
            ctx=RunContext(workspace=tmp_path),
            settings=settings,
            system="",
            prompt_hash="oops",
        )
    assert llm.messages.calls == []


def test_the_hash_of_a_rendered_prompt_goes_through_to_the_log(settings: Settings, tmp_path: Path) -> None:
    profiles = load_profiles(ROOT / "permissions.yaml")
    tools = build_app(settings, llm=FakeLLM()).tools.to_api_tools(profiles["review"].allow)
    rendered = render_system(tools=tools, contract_text=None)
    log = tmp_path / "trajectory.jsonl"
    llm = FakeLLM([text_message("done")])

    run_agent(
        render_user_context("find the bug"),
        llm=llm,
        tools=make_tool_registry(),
        ctx=RunContext(workspace=tmp_path),
        settings=settings,
        system=rendered.system,
        trajectory=TrajectoryWriter(log, run_id="r", clock=DatetimeClock(), redactor=Redactor()),
        prompt_hash=rendered.hash,
    )

    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert first["prompt_hash"] == rendered.hash
    sent = llm.messages.calls[0]
    assert sent.system == rendered.system  # what was sent is what was hashed
    assert sent.messages[0]["content"].startswith("<untrusted_issue>")  # type: ignore[union-attr, index]
