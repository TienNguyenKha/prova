"""Record and replay: a client that writes down what the model said, and one that says it again.

Replay is how the harness is tested without the model: the same answers come back for the same questions,
and a question nobody recorded is an error and never a call to the API (the tests block the network, so a
replay that tried would fail here too). A cassette is a file of the model's answers, so it goes through the
redactor before it reaches the disk.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from anthropic import omit
from anthropic.types import TextBlock

from prova.agent.loop import run_agent
from prova.config import Settings
from prova.errors import LLMError
from prova.harness.redact import Redactor
from prova.llm.cassette import (
    CassetteError,
    CassetteMiss,
    RecordingClient,
    ReplayClient,
    request_hash,
)
from prova.tools.base import RunContext
from tests.fakes import EchoTool, FakeLLM, make_tool_registry, text_message, tool_use_message

ASK: dict[str, Any] = {
    "model": "m", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}],
    "system": "be careful", "output_config": None, "tools": None,
}  # fmt: skip


def ask(**changes: Any) -> dict[str, Any]:
    return {**ASK, **changes}


def hashed(**changes: Any) -> str:
    return request_hash(**ask(**changes))


# --- the hash of a request ------------------------------------------------------------------------


def test_the_hash_is_a_sha256_and_the_same_request_gives_the_same_one() -> None:
    assert len(hashed()) == 64 and hashed() == hashed()


@pytest.mark.parametrize(
    "change",
    [
        {"model": "other"},
        {"max_tokens": 101},
        {"messages": [{"role": "user", "content": "ho"}]},
        {"system": "be bold"},
        {"output_config": {"format": "json"}},
        {"tools": [{"name": "echo"}]},
    ],
)
def test_a_change_in_any_part_of_the_request_changes_the_hash(change: dict[str, Any]) -> None:
    assert hashed(**change) != hashed()


def test_the_order_of_the_keys_does_not_matter() -> None:
    one = {"role": "user", "content": [{"type": "text", "text": "a"}]}
    other = {"content": [{"text": "a", "type": "text"}], "role": "user"}
    assert hashed(messages=[one]) == hashed(messages=[other])


def test_an_omitted_part_is_the_same_as_a_part_that_is_none() -> None:
    assert hashed(system=omit, tools=omit, output_config=omit) == hashed(system=None)


def test_a_block_object_and_its_dict_have_the_same_hash() -> None:
    """The loop sends back the blocks the model returned, as objects; a cassette reads them as dicts."""
    as_object = {"role": "assistant", "content": [TextBlock(type="text", text="hi", citations=None)]}
    as_dict = {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
    assert hashed(messages=[as_object]) == hashed(messages=[as_dict])


# --- durations in what a tool said --------------------------------------------------------------------


def tool_says(text: str | list[dict[str, str]]) -> list[dict[str, Any]]:
    result = {"type": "tool_result", "tool_use_id": "a", "content": text}
    return [{"role": "user", "content": [result]}]


def user_says(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("ran tests (exit code 1, 0.6s)", "ran tests (exit code 1, 0.7s)"),
        ("1 failed, 2 passed in 0.03s", "1 failed, 2 passed in 12.58s"),
        ("1 passed in 61.00s (0:01:01)", "1 passed in 75.40s (0:01:15)"),
    ],
)
def test_how_long_a_tool_took_does_not_change_the_hash(one: str, other: str) -> None:
    assert hashed(messages=tool_says(one)) == hashed(messages=tool_says(other))


def test_the_same_holds_for_a_tool_result_made_of_blocks() -> None:
    blocks = [{"type": "text", "text": "ran tests (0.6s)"}], [{"type": "text", "text": "ran tests (0.9s)"}]
    assert hashed(messages=tool_says(blocks[0])) == hashed(messages=tool_says(blocks[1]))


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("1 passed", "2 passed"),  # a real difference stays one
        ("timeout is 120s", "timeout is 60s"),  # a whole number of seconds is a setting, not a measurement
        ("took 0.6 seconds", "took 0.9 seconds"),  # only the form the tools print is a duration
    ],
)
def test_a_real_difference_in_what_a_tool_said_still_changes_the_hash(one: str, other: str) -> None:
    assert hashed(messages=tool_says(one)) != hashed(messages=tool_says(other))


def test_a_duration_outside_a_tool_result_still_counts() -> None:
    """Only what a tool measured is ignored: the user's words and the system prompt are not."""
    assert hashed(messages=user_says("wait 0.6s")) != hashed(messages=user_says("wait 0.9s"))
    assert hashed(system="limit 0.6s") != hashed(system="limit 0.9s")


# --- recording ------------------------------------------------------------------------------------


def record(path: Path, replies: list[Any], redactor: Redactor | None = None) -> RecordingClient:
    return RecordingClient(FakeLLM(replies), path, redactor or Redactor())


def test_a_recorded_call_returns_the_real_answer_and_adds_one_line_to_the_file(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "c.jsonl"  # the folder does not exist yet
    answer = text_message("hello")
    got = record(path, [answer]).messages.create(**ask())
    assert got == answer
    (line,) = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert entry["schema_version"] == 1 and entry["request_hash"] == hashed()
    assert entry["response"]["content"][0]["text"] == "hello"


def test_every_call_is_written_when_it_happens_and_the_file_only_grows(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    client = record(path, [text_message("a"), text_message("b")])
    client.messages.create(**ask())
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    client.messages.create(**ask(model="other"))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_an_error_is_not_an_answer_so_nothing_is_recorded_and_the_error_goes_on(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    with pytest.raises(LLMError):
        record(path, [LLMError("boom")]).messages.create(**ask())
    assert not path.exists()


def test_what_the_redactor_hides_is_not_in_the_file(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    secret = "sk-ant-api03-THIS-IS-A-FAKE-KEY-0123456789"  # noqa: S105 - a fake key, to see it hidden
    record(path, [text_message(f"the key is {secret}")]).messages.create(**ask())
    assert secret not in path.read_text(encoding="utf-8")


# --- replay ---------------------------------------------------------------------------------------


def recorded(tmp_path: Path, *calls: tuple[dict[str, Any], str]) -> Path:
    path = tmp_path / "c.jsonl"
    client = record(path, [text_message(text) for _, text in calls])
    for request, _ in calls:
        client.messages.create(**request)
    return path


def test_replay_gives_back_the_recorded_answer_for_the_same_request(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "hello"))
    assert ReplayClient(path).messages.create(**ask()) == text_message("hello")


def test_a_request_nobody_recorded_is_a_miss_that_says_which_and_never_a_call(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "hello"))
    with pytest.raises(CassetteMiss, match="no answer.*other-model"):
        ReplayClient(path).messages.create(**ask(model="other-model"))


def test_the_same_request_twice_gets_the_answers_in_the_order_they_were_given(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "first"), (ask(), "second"))
    client = ReplayClient(path)
    assert client.messages.create(**ask()).content[0].text == "first"  # type: ignore[union-attr]
    assert client.messages.create(**ask()).content[0].text == "second"  # type: ignore[union-attr]


def test_asking_more_times_than_it_was_recorded_is_a_miss_and_not_a_repeat(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "once"))
    client = ReplayClient(path)
    client.messages.create(**ask())
    with pytest.raises(CassetteMiss, match="already"):
        client.messages.create(**ask())


def test_what_was_recorded_and_never_asked_for_is_reported(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "a"), (ask(model="other"), "b"))
    client = ReplayClient(path)
    client.messages.create(**ask())
    assert client.unused == 1


# --- a cassette that cannot be trusted ------------------------------------------------------------


def test_a_cassette_that_is_not_there_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CassetteError, match="cannot read"):
        ReplayClient(tmp_path / "missing.jsonl")


def test_blank_lines_in_a_cassette_are_skipped(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "hello"))
    path.write_text("\n" + path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert ReplayClient(path).messages.create(**ask()) == text_message("hello")


def test_a_line_that_is_not_json_is_an_error_that_names_the_line(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "a"))
    path.write_text(path.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    with pytest.raises(CassetteError, match="line 2"):
        ReplayClient(path)


def test_a_cassette_of_another_version_is_refused(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "a"))
    entry = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**entry, "schema_version": 99}) + "\n", encoding="utf-8")
    with pytest.raises(CassetteError, match="version"):
        ReplayClient(path)


def test_an_entry_with_no_response_is_an_error_and_not_a_silent_skip(tmp_path: Path) -> None:
    path = recorded(tmp_path, (ask(), "a"))
    entry = json.loads(path.read_text(encoding="utf-8"))
    del entry["response"]
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    with pytest.raises(CassetteError, match="line 1"):
        ReplayClient(path)


# --- the same clients in a real run ---------------------------------------------------------------


def test_a_run_recorded_and_replayed_has_the_same_answer_and_the_same_tool_calls(
    tmp_path: Path, settings: Settings
) -> None:
    replies = [
        tool_use_message(("a", "echo", {"text": "x", "times": 2})),
        text_message("the tool said xx"),
    ]

    def run(llm: Any) -> tuple[str, list[str]]:
        result = run_agent(
            "find the answer", llm=llm, tools=make_tool_registry(EchoTool()),
            ctx=RunContext(workspace=tmp_path, strict=True), settings=settings, system="be careful",
        )  # fmt: skip
        return result.final_text, [r.turn.__str__() for r in result.usage.records]

    path = tmp_path / "c.jsonl"
    first = run(RecordingClient(FakeLLM(replies), path, Redactor()))
    replay = ReplayClient(path)
    assert run(replay) == first
    assert replay.unused == 0
