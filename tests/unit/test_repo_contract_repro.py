"""The contract of the reproduction role (contract/repro/), checked against the configuration it is about.

A reproduction run does the opposite of a fix run: it writes tests, only in repro/, and is done when they FAIL
for the right reason. So it has its own contract, with the same form and the same guarantees as the one of a
fix run (see test_repo_contract.py): every rule holds on the real profile and hooks, and the two files say the
same thing word for word.
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
from prova.harness.verifiers import FilesListed, LintAfterEdit, ReproFailsBeforeStop, TestsBeforeStop
from prova.mechanisms import builtin_mechanisms
from tests.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contract" / "repro" / "contract.yaml"
AGENTS = ROOT / "contract" / "repro" / "AGENTS.md"
SECTIONS = ["Identity", "You MAY", "You MUST NOT", "Definition of done", "Output contract", "Escalate"]
ONLY_REQUESTS = ("test_behaviour", "no_guessing")
REPRO_HOOKS = Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), ReproFailsBeforeStop()))


def real_context(settings: Settings, **changes: object) -> VerifyContext:
    ctx = VerifyContext(
        profiles=load_profiles(ROOT / "permissions.yaml"),
        hooks=REPRO_HOOKS,
        tools=build_app(settings, llm=FakeLLM()).tools,
        project_root=ROOT,
    )
    return dataclasses.replace(ctx, **changes)


# --- It holds -------------------------------------------------------------------------------------


def test_the_contract_holds_on_the_real_profile_and_hooks_of_a_reproduction_run(settings: Settings) -> None:
    report = check_contract(load_contract(CONTRACT), builtin_mechanisms(), real_context(settings))
    assert report.prompt_only == ONLY_REQUESTS


def test_every_rule_that_can_be_enforced_is() -> None:
    enforced = {r.id for r in load_contract(CONTRACT).must_not if r.enforced_by != "prompt_only"}
    assert enforced == {
        "no_code_edits",
        "no_secret_reads",
        "no_network_tools",
        "no_network_in_commands",
        "no_secrets_in_logs",
    }


def test_the_definition_of_done_is_that_the_tests_show_the_bug() -> None:
    ids = [rule.id for rule in load_contract(CONTRACT).definition_of_done]
    assert ids == ["repro_fails", "lint_checked", "report_submitted", "files_listed"]


# --- It breaks when the configuration stops backing it --------------------------------------------


def test_a_repro_profile_that_may_write_the_code_makes_the_contract_fail(settings: Settings) -> None:
    profiles = load_profiles(ROOT / "permissions.yaml")
    loose = profiles["repro"].model_copy(update={"write_paths": ("**",)})
    ctx = real_context(settings, profiles={**profiles, "repro": loose})
    with pytest.raises(ContractError, match=r"no_code_edits.*lets .* be written"):
        check_contract(load_contract(CONTRACT), builtin_mechanisms(), ctx)


def test_a_reproduction_run_with_the_gate_of_a_fix_run_fails_the_contract(settings: Settings) -> None:
    hooks = Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), TestsBeforeStop()))  # wants the tests green
    with pytest.raises(ContractError, match="repro_fails"):
        check_contract(load_contract(CONTRACT), builtin_mechanisms(), real_context(settings, hooks=hooks))


# --- The two files say the same thing, and the model reads a short structured file ---------------


def test_every_rule_of_the_yaml_is_in_the_agents_file_word_for_word() -> None:
    contract = load_contract(CONTRACT)
    agents = AGENTS.read_text(encoding="utf-8")
    for rule in (*contract.must_not, *contract.definition_of_done):
        assert rule.text in agents, f"{rule.id}: its text is not in contract/repro/AGENTS.md"


def test_every_escalation_and_output_field_is_explained_in_the_agents_file() -> None:
    contract = load_contract(CONTRACT)
    agents = AGENTS.read_text(encoding="utf-8")
    for name in (*contract.escalate_when, *contract.output_contract):
        assert f"`{name}`" in agents, name


def test_the_agents_file_has_every_section_in_order() -> None:
    headings = re.findall(r"^## (.+)$", AGENTS.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert headings == SECTIONS


def test_the_agents_file_says_where_the_tests_go_and_that_they_must_fail() -> None:
    agents = AGENTS.read_text(encoding="utf-8")
    assert "repro/" in agents and "fail" in agents
