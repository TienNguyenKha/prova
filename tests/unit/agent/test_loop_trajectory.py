"""run_agent writes a trajectory: what happened, in order, with secrets removed."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from prova.agent.loop import run_agent
from prova.config import Settings
from prova.errors import LLMError, TrajectoryError
from prova.harness.permissions import Permission
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.tools.base import RunContext, Tool, ToolResult
from prova.tools.fs import ReadFileTool
from tests.fakes import (
    EchoArgs,
    EchoTool,
    FakeLLM,
    WriterTool,
    build_message,
    make_tool_registry,
    text_message,
    tool_use_message,
)

ANTHROPIC_LIKE = "sk-ant-api03-AbC123_xyz-9876-QwErTy"  # the shape of a key, not a real one


class DatetimeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(seconds=1)
        return current


class LeakyTool:
    """A tool whose output contains something that looks like a key."""

    name = "leaky"
    description = "Returns a secret by mistake."
    args_model = EchoArgs
    permission = Permission.READ

    def run(self, args: EchoArgs, ctx: RunContext) -> ToolResult:
        return ToolResult.ok(f"found {ANTHROPIC_LIKE} in the file")


def run(
    llm: FakeLLM,
    settings: Settings,
    workspace: Path,
    *tools: Tool,
    log: Path,
    redactor: Redactor | None = None,
    max_iterations: int = 30,
) -> list[dict[str, Any]]:
    """Run the agent with a trajectory at `log`, and return the records it wrote."""
    writer = TrajectoryWriter(log, run_id="run-1", clock=DatetimeClock(), redactor=Redactor())
    run_agent(
        "find the answer",
        llm=llm,
        tools=make_tool_registry(*tools),
        ctx=RunContext(workspace=workspace, strict=True),
        settings=settings,
        system="You are careful.",
        max_iterations=max_iterations,
        trajectory=writer,
        redactor=redactor,
    )
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def types_of(records: list[dict[str, Any]]) -> list[str]:
    return [r["type"] for r in records]


# --- A run is told in order --------------------------------------------------


def test_a_plain_answer_is_logged_in_order(settings: Settings, tmp_path: Path) -> None:
    records = run(FakeLLM([text_message("42")]), settings, tmp_path, EchoTool(), log=tmp_path / "t.jsonl")

    assert types_of(records) == ["task", "model_request", "model_response", "outcome"]
    task, request, response, outcome = records
    assert task["task"] == "find the answer"
    assert (request["turn"], request["model"], request["messages"], request["tools"]) == (
        1,
        settings.model,
        1,
        ["echo"],
    )
    assert (response["stop_reason"], response["text"], response["tool_calls"]) == ("end_turn", "42", 0)
    assert (response["input_tokens"], response["output_tokens"]) == (1, 1)
    assert (outcome["status"], outcome["iterations"], outcome["input_tokens"]) == ("done", 1, 1)


def test_the_sequence_numbers_have_no_gaps(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([tool_use_message(("t1", "echo", {"text": "x"})), text_message("done")])
    records = run(llm, settings, tmp_path, EchoTool(), log=tmp_path / "t.jsonl")
    assert [r["seq"] for r in records] == list(range(len(records)))


def test_a_tool_call_is_logged_with_its_call_then_its_result_then_the_next_request(
    settings: Settings, tmp_path: Path
) -> None:
    llm = FakeLLM([tool_use_message(("t1", "echo", {"text": "hi"})), text_message("done")])
    records = run(llm, settings, tmp_path, EchoTool(), log=tmp_path / "t.jsonl")

    assert types_of(records) == [
        "task",
        "model_request",
        "model_response",
        "tool_call",
        "tool_result",
        "model_request",
        "model_response",
        "outcome",
    ]
    call, result = records[3], records[4]
    assert (call["tool"], call["tool_use_id"], call["input"], call["decision"]) == (
        "echo",
        "t1",
        {"text": "hi"},
        "allow",
    )
    shown = (result["success"], result["error_code"], result["message"], result["chars"])
    assert shown == (True, None, "hi", 2)
    assert records[2]["tool_calls"] == 1
    assert records[5]["messages"] == 3  # the task, the assistant turn, and the tool results


def test_two_tool_calls_in_one_turn_are_logged_one_call_and_result_after_the_other(
    settings: Settings, tmp_path: Path
) -> None:
    calls = (("t1", "echo", {"text": "a"}), ("t2", "echo", {"text": "b"}))
    llm = FakeLLM([tool_use_message(*calls), text_message("done")])
    records = run(llm, settings, tmp_path, EchoTool(), log=tmp_path / "t.jsonl")

    assert records[2]["tool_calls"] == 2
    assert types_of(records)[3:7] == ["tool_call", "tool_result", "tool_call", "tool_result"]
    assert [r["tool_use_id"] for r in records[3:7]] == ["t1", "t1", "t2", "t2"]


# --- The decision is the harness's decision, not the tool's ----------------


def test_a_tool_outside_the_runs_permissions_is_logged_as_denied(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([tool_use_message(("t1", "write_thing", {"text": "x"})), text_message("ok")])
    records = run(llm, settings, tmp_path, EchoTool(), WriterTool(), log=tmp_path / "t.jsonl")

    call, result = records[3], records[4]
    assert call["decision"] == "deny"
    assert (result["success"], result["error_code"]) == (False, "permission_denied")


def test_a_tool_that_refuses_a_path_was_still_allowed_to_run(settings: Settings, tmp_path: Path) -> None:
    # The harness allowed `read_file`; it was the PATH that was protected. The log must tell the two apart.
    (tmp_path / ".env").write_text("KEY=1", encoding="utf-8")
    llm = FakeLLM([tool_use_message(("t1", "read_file", {"path": ".env"})), text_message("ok")])
    records = run(llm, settings, tmp_path, ReadFileTool(), log=tmp_path / "t.jsonl")

    call, result = records[3], records[4]
    assert call["decision"] == "allow"
    assert result["error_code"] == "permission_denied"


def test_an_unknown_tool_is_allowed_and_fails_as_not_found(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([tool_use_message(("t1", "no_such_tool", {})), text_message("ok")])
    records = run(llm, settings, tmp_path, EchoTool(), log=tmp_path / "t.jsonl")

    assert records[3]["decision"] == "allow"
    assert records[4]["error_code"] == "not_found"


# --- Every way a run can end is logged ----------------------------------------


def _forever() -> list[Any]:
    return [tool_use_message((f"t{i}", "echo", {"text": "x"})) for i in range(3)]


@pytest.mark.parametrize(
    ("replies", "max_iterations", "status"),
    [
        ([text_message("done")], 30, "done"),
        (_forever(), 3, "max_iterations"),
        ([text_message("cut", stop_reason="max_tokens")], 30, "max_tokens"),
        ([text_message("no", stop_reason="refusal")], 30, "refused"),
        ([text_message("hm", stop_reason="pause_turn")], 30, "error"),
    ],
    ids=["done", "max_iterations", "max_tokens", "refused", "unhandled stop_reason"],
)
def test_every_status_ends_with_an_outcome_that_says_which(
    settings: Settings, tmp_path: Path, replies: list[Any], max_iterations: int, status: str
) -> None:
    log = tmp_path / "t.jsonl"
    records = run(FakeLLM(replies), settings, tmp_path, EchoTool(), log=log, max_iterations=max_iterations)
    assert records[-1]["type"] == "outcome"
    assert records[-1]["status"] == status
    assert types_of(records).count("outcome") == 1


def test_a_failed_model_call_is_logged_as_a_request_and_an_error(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM([tool_use_message(("t1", "echo", {"text": "x"})), LLMError("overloaded", status_code=503)])
    records = run(llm, settings, tmp_path, EchoTool(), log=tmp_path / "t.jsonl")

    assert types_of(records)[-2:] == ["model_request", "outcome"]  # asked, never answered
    assert records[-1]["status"] == "error"
    assert "overloaded" in records[-1]["detail"] and "503" in records[-1]["detail"]


# --- A run that cannot be logged does not run ---------------------------------


def test_if_the_log_cannot_be_written_the_model_is_never_called(settings: Settings, tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    llm = FakeLLM([text_message("42")])

    with pytest.raises(TrajectoryError):
        run(llm, settings, tmp_path, EchoTool(), log=blocker / "t.jsonl")
    assert llm.messages.calls == []  # the task event comes first, so no model call went unlogged


# --- Secrets: in the log, and in what the model is shown ---------------------


def test_a_secret_in_a_tool_result_is_hidden_from_the_model_and_from_the_log(
    settings: Settings, tmp_path: Path
) -> None:
    llm = FakeLLM([tool_use_message(("t1", "leaky", {"text": "x"})), text_message("ok")])
    log = tmp_path / "t.jsonl"
    records = run(llm, settings, tmp_path, LeakyTool(), log=log)

    shown_to_the_model = llm.messages.calls[1].messages[2]["content"][0]["content"]  # type: ignore[index]
    assert shown_to_the_model == "found [REDACTED] in the file"
    assert ANTHROPIC_LIKE not in log.read_text(encoding="utf-8")
    assert records[4]["chars"] == len("found [REDACTED] in the file")  # chars counts what the model was shown


def test_values_of_sensitive_environment_variables_are_hidden_from_the_model_too(
    settings: Settings, tmp_path: Path
) -> None:
    class Plain:
        name = "plain"
        description = "Returns a value with no known shape."
        args_model = EchoArgs
        permission = Permission.READ

        def run(self, args: EchoArgs, ctx: RunContext) -> ToolResult:
            return ToolResult.ok("the value is a-value-without-any-shape")

    redactor = Redactor.from_environ({"SOME_SERVICE_KEY": "a-value-without-any-shape"})
    llm = FakeLLM([tool_use_message(("t1", "plain", {"text": "x"})), text_message("ok")])
    run(llm, settings, tmp_path, Plain(), log=tmp_path / "t.jsonl", redactor=redactor)

    shown = llm.messages.calls[1].messages[2]["content"][0]["content"]  # type: ignore[index]
    assert shown == "the value is [REDACTED]"


def test_a_tool_input_that_is_not_an_object_is_logged_as_text_instead_of_breaking_the_log(
    settings: Settings, tmp_path: Path
) -> None:
    odd = build_message([{"type": "tool_use", "id": "t1", "name": "echo", "input": {}}], "tool_use")
    odd.content[0].input = "just text"  # type: ignore[union-attr]  # not validated
    llm = FakeLLM([odd, text_message("ok")])
    records = run(llm, settings, tmp_path, EchoTool(), log=tmp_path / "t.jsonl")

    assert records[3]["input"] == {"_not_an_object": "'just text'"}
    assert records[4]["error_code"] == "invalid_args"
