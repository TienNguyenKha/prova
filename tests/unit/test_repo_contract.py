"""The contract of this repository (contract/), checked against the configuration it is about.

The contract is two files: `contract.yaml` for code and `AGENTS.md` for the model. They must say the same
thing, and what they say must hold on the real profiles, hooks and sandbox. If either stops being true, a
test here goes red, and so does the start of a run.
"""

import dataclasses
import re
from pathlib import Path

import pytest

from prova.app import build_app
from prova.config import Settings
from prova.contract import VerifyContext, check_contract, load_contract
from prova.errors import ContractError
from prova.harness.hooks import Hooks
from prova.harness.permissions import load_profiles
from prova.harness.verifiers import FilesListed, LintAfterEdit, TestsBeforeStop
from prova.mechanisms import builtin_mechanisms
from tests.fakes import FakeLLM, make_tool_registry

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contract" / "contract.yaml"
AGENTS = ROOT / "contract" / "AGENTS.md"
SECTIONS = ["Identity", "You MAY", "You MUST NOT", "Definition of done", "Output contract", "Escalate"]

# The rules that nothing enforces. Writing them here is the point: adding one is a decision somebody makes.
ONLY_REQUESTS = ("stay_in_scope", "no_guessing")


def real_context(settings: Settings, **changes: object) -> VerifyContext:
    """The configuration of a real run: the profiles of the repo, the tools `build_app` really registers, and
    the hooks a run is meant to have. The report check goes first: it is free, the test run is not."""
    ctx = VerifyContext(
        profiles=load_profiles(ROOT / "permissions.yaml"),
        hooks=Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), TestsBeforeStop())),
        tools=build_app(settings, llm=FakeLLM()).tools,
        project_root=ROOT,
    )
    return dataclasses.replace(ctx, **changes)


# --- It holds -------------------------------------------------------------------------------------


def test_the_contract_holds_on_the_real_configuration(settings: Settings) -> None:
    report = check_contract(load_contract(CONTRACT), builtin_mechanisms(), real_context(settings))
    assert {r.status for r in report.rules} == {"enforced", "prompt_only"}


def test_the_rules_that_are_only_requests_are_exactly_the_ones_we_know_about(settings: Settings) -> None:
    report = check_contract(load_contract(CONTRACT), builtin_mechanisms(), real_context(settings))
    assert report.prompt_only == ONLY_REQUESTS


def test_every_rule_that_can_be_enforced_is() -> None:
    contract = load_contract(CONTRACT)
    enforced = {r.id for r in contract.must_not if r.enforced_by != "prompt_only"}
    assert {
        "no_test_edits",
        "no_secret_reads",
        "no_network_tools",
        "no_network_in_commands",
        "no_secrets_in_logs",
    } <= enforced


# --- It breaks when the configuration stops backing it --------------------------------------------


def test_a_fix_profile_that_may_write_tests_makes_the_contract_fail_and_names_the_rule(
    settings: Settings,
) -> None:
    profiles = load_profiles(ROOT / "permissions.yaml")
    weakened = profiles["fix"].model_copy(update={"write_deny_paths": ()})
    ctx = real_context(settings, profiles={**profiles, "fix": weakened})
    with pytest.raises(ContractError, match=r"no_test_edits.*lets .* be written"):
        check_contract(load_contract(CONTRACT), builtin_mechanisms(), ctx)


def test_a_run_without_its_hooks_fails_the_contract_and_names_all_of_them(settings: Settings) -> None:
    with pytest.raises(ContractError) as caught:
        check_contract(load_contract(CONTRACT), builtin_mechanisms(), real_context(settings, hooks=Hooks()))
    message = str(caught.value)
    assert "tests_green" in message and "lint_checked" in message and "files_listed" in message


def test_a_run_without_the_report_check_fails_the_contract(settings: Settings) -> None:
    hooks = Hooks(post=(LintAfterEdit(),), stop=(TestsBeforeStop(),))  # no FilesListed
    with pytest.raises(ContractError, match="files_listed"):
        check_contract(load_contract(CONTRACT), builtin_mechanisms(), real_context(settings, hooks=hooks))


def test_a_run_whose_agent_has_no_submit_result_tool_fails_the_contract(settings: Settings) -> None:
    ctx = real_context(settings, tools=make_tool_registry())  # no tools at all
    with pytest.raises(ContractError, match="report_submitted"):
        check_contract(load_contract(CONTRACT), builtin_mechanisms(), ctx)


def test_the_definition_of_done_is_exactly_what_we_expect() -> None:
    ids = [rule.id for rule in load_contract(CONTRACT).definition_of_done]
    assert ids == ["tests_green", "lint_checked", "report_submitted", "files_listed"]


def test_the_agents_file_tells_the_model_to_hand_in_the_report_with_the_tool() -> None:
    output_section = AGENTS.read_text(encoding="utf-8").split("## Output contract")[1].split("## ")[0]
    assert "`submit_result`" in output_section


# --- The two files say the same thing -------------------------------------------------------------


def test_every_rule_of_the_yaml_is_in_the_agents_file_word_for_word() -> None:
    contract = load_contract(CONTRACT)
    agents = AGENTS.read_text(encoding="utf-8")
    for rule in (*contract.must_not, *contract.definition_of_done):
        assert rule.text in agents, f"{rule.id}: its text is not in AGENTS.md"


def test_every_escalation_in_the_yaml_is_explained_in_the_agents_file() -> None:
    agents = AGENTS.read_text(encoding="utf-8")
    for name in load_contract(CONTRACT).escalate_when:
        assert f"`{name}`" in agents, name


def test_every_output_field_in_the_yaml_is_asked_for_in_the_agents_file() -> None:
    agents = AGENTS.read_text(encoding="utf-8")
    for name in load_contract(CONTRACT).output_contract:
        assert f"`{name}`" in agents, name


# --- The file the model reads is short and structured ---------------------------------------------


def test_the_agents_file_has_every_section_in_order() -> None:
    headings = re.findall(r"^## (.+)$", AGENTS.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert headings == SECTIONS


def test_the_agents_file_is_short_enough_to_be_read_as_rules_and_not_as_advice() -> None:
    words = len(AGENTS.read_text(encoding="utf-8").split())
    assert words <= 600, f"{words} words: a contract this long gets read as a suggestion"


def test_the_must_not_section_has_one_numbered_item_per_rule() -> None:
    must_not = AGENTS.read_text(encoding="utf-8").split("## You MUST NOT")[1].split("## ")[0]
    assert len(re.findall(r"^\d+\. ", must_not, flags=re.MULTILINE)) == len(load_contract(CONTRACT).must_not)
