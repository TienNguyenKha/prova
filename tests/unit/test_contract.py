"""Tests for prova.contract: the rules of the agent, written so that a machine can check them.

A rule is only worth its place if something enforces it. At start-up every rule is tied to a mechanism,
and the mechanism is asked to prove it works on the configuration this run really has. A rule that is only
a request to the model says so (`prompt_only`) instead of pretending.
"""

import textwrap
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from prova.contract import (
    PROMPT_ONLY,
    Contract,
    VerifyContext,
    check_contract,
    load_contract,
)
from prova.errors import ConfigError, ContractError
from prova.harness.hooks import Hooks
from tests.fakes import make_tool_registry

VALID = """
    version: 1
    must_not:
      - id: no_test_edits
        text: Do not modify files under tests/.
        enforced_by: permissions.write_blocked
        params: {profile: fix, path: tests/x.py}
      - id: stay_in_scope
        text: Only change what the task asks for.
        enforced_by: prompt_only
    definition_of_done:
      - id: tests_green
        text: The tests pass.
        check: hook.stop.tests
    output_contract: [files_changed_with_reason, test_command, assumptions]
    escalate_when: [ambiguous_scope, requires_secret]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "contract.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def verify_context(tmp_path: Path) -> VerifyContext:
    return VerifyContext(profiles={}, hooks=Hooks(), tools=make_tool_registry(), project_root=tmp_path)


# --- Reading the file -----------------------------------------------------------------


def test_a_valid_file_gives_the_rules_in_order(tmp_path: Path) -> None:
    contract = load_contract(write(tmp_path, VALID))
    assert [r.id for r in contract.must_not] == ["no_test_edits", "stay_in_scope"]
    assert contract.must_not[0].enforced_by == "permissions.write_blocked"
    assert dict(contract.must_not[0].params) == {"profile": "fix", "path": "tests/x.py"}
    assert [r.id for r in contract.definition_of_done] == ["tests_green"]
    assert contract.definition_of_done[0].check == "hook.stop.tests"
    assert contract.output_contract == ("files_changed_with_reason", "test_command", "assumptions")
    assert contract.escalate_when == ("ambiguous_scope", "requires_secret")


def test_a_rule_that_is_only_a_request_is_marked_as_such(tmp_path: Path) -> None:
    contract = load_contract(write(tmp_path, VALID))
    assert contract.must_not[1].enforced_by == PROMPT_ONLY == "prompt_only"


def test_a_rule_with_no_params_has_empty_params(tmp_path: Path) -> None:
    assert load_contract(write(tmp_path, VALID)).must_not[1].params == {}


def test_the_contract_cannot_be_changed_after_loading(tmp_path: Path) -> None:
    contract = load_contract(write(tmp_path, VALID))
    assert isinstance(contract, Contract)
    with pytest.raises(FrozenInstanceError):
        contract.version = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        contract.must_not[0].enforced_by = "nothing"  # type: ignore[misc]
    with pytest.raises(TypeError):
        contract.must_not[0].params["profile"] = "review"  # type: ignore[index]


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("version: 1", "version: 2", "version"),
        ("id: no_test_edits", "id: No Test Edits", "id"),
        ("id: stay_in_scope", "id: no_test_edits", "twice"),  # duplicate id
        ("enforced_by: prompt_only", "enforced_by: ''", "enforced_by"),
        ("text: Only change what the task asks for.", "text: ''", "text"),
        (
            "[files_changed_with_reason, test_command, assumptions]",
            "[files_changed_with_reason, poetry]",
            "poetry",
        ),
        ("enforced_by: prompt_only", "enforced_by_typo: prompt_only", "enforced_by_typo"),
        ("check: hook.stop.tests", "check: ''", "check"),
    ],
)
def test_a_contract_with_something_wrong_in_it_is_a_config_error(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        load_contract(write(tmp_path, VALID.replace(old, new)))


def test_a_missing_file_says_which_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="contract.yaml"):
        load_contract(tmp_path / "contract.yaml")


@pytest.mark.parametrize("text", ["", "- a\n", "must_not: []\n", "version: 1\nmust_not: oops\n"])
def test_a_file_of_the_wrong_shape_is_a_config_error(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        load_contract(write(tmp_path, text))


def test_yaml_cannot_run_code_while_it_is_loaded(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    text = f"version: !!python/object/apply:pathlib.Path.touch [{marker.as_posix()!r}]\n"
    with pytest.raises(ConfigError):
        load_contract(write(tmp_path, text))
    assert not marker.exists()


# --- Checking that every rule is backed by something --------------------------------------


def working(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
    return None


def test_when_every_mechanism_proves_itself_the_contract_holds_and_the_requests_are_listed(
    tmp_path: Path,
) -> None:
    contract = load_contract(write(tmp_path, VALID))
    mechanisms = {"permissions.write_blocked": working, "hook.stop.tests": working}

    report = check_contract(contract, mechanisms, verify_context(tmp_path))

    assert [(r.rule, r.status) for r in report.rules] == [
        ("no_test_edits", "enforced"),
        ("stay_in_scope", "prompt_only"),
        ("tests_green", "enforced"),
    ]
    assert report.prompt_only == ("stay_in_scope",)


def test_a_mechanism_is_given_the_params_of_the_rule_and_the_real_context(tmp_path: Path) -> None:
    seen: list[tuple[VerifyContext, Mapping[str, Any]]] = []

    def spy(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
        seen.append((ctx, params))
        return None

    ctx = verify_context(tmp_path)
    check_contract(
        load_contract(write(tmp_path, VALID)),
        {"permissions.write_blocked": spy, "hook.stop.tests": working},
        ctx,
    )
    assert seen[0][0] is ctx and dict(seen[0][1]) == {"profile": "fix", "path": "tests/x.py"}


def test_a_rule_tied_to_a_mechanism_that_does_not_exist_stops_the_start(tmp_path: Path) -> None:
    contract = load_contract(write(tmp_path, VALID))
    with pytest.raises(ContractError, match=r"no_test_edits.*permissions\.write_blocked.*no such mechanism"):
        check_contract(contract, {"hook.stop.tests": working}, verify_context(tmp_path))


def test_a_mechanism_that_says_it_does_not_hold_stops_the_start_and_says_why(tmp_path: Path) -> None:
    def leaky(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
        return "profile 'fix' lets tests/x.py be written"

    with pytest.raises(ContractError, match=r"no_test_edits.*lets tests/x.py be written"):
        check_contract(
            load_contract(write(tmp_path, VALID)),
            {"permissions.write_blocked": leaky, "hook.stop.tests": working},
            verify_context(tmp_path),
        )


def test_a_mechanism_that_crashes_counts_as_not_holding(tmp_path: Path) -> None:
    def crashes(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
        raise KeyError("fix")

    with pytest.raises(ContractError, match=r"hook\.stop\.tests.*KeyError"):
        check_contract(
            load_contract(write(tmp_path, VALID)),
            {"permissions.write_blocked": working, "hook.stop.tests": crashes},
            verify_context(tmp_path),
        )


def test_every_problem_is_reported_at_once_not_just_the_first(tmp_path: Path) -> None:
    def broken(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
        return "does not hold"

    with pytest.raises(ContractError) as caught:
        check_contract(
            load_contract(write(tmp_path, VALID)),
            {"permissions.write_blocked": broken},  # and hook.stop.tests is missing altogether
            verify_context(tmp_path),
        )
    message = str(caught.value)
    assert "no_test_edits" in message and "tests_green" in message


def test_a_contract_with_only_requests_holds_but_says_so(tmp_path: Path) -> None:
    text = """
        version: 1
        must_not:
          - id: be_nice
            text: Be nice.
            enforced_by: prompt_only
    """
    report = check_contract(load_contract(write(tmp_path, text)), {}, verify_context(tmp_path))
    assert report.prompt_only == ("be_nice",)
