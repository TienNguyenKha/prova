"""The contracts of the other roles (contract/<role>/), checked against the configuration they are about.

A reproduction run (M13b) writes tests in repro/ and is done when they FAIL for the right reason. An adversary
(M14) writes tests in attack/ that try to break a patch, and is done once it has tried; it gets nothing for
agreeing. Each role has its own contract, with the same form and the same guarantees as the one of a fix run
(see test_repo_contract.py): every rule holds on the real profile and hooks, and the two files say the same
thing word for word.
"""

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from prova.app import build_app
from prova.config import Settings
from prova.contract import VerifyContext, check_contract, load_contract
from prova.errors import ContractError
from prova.harness.hooks import Hook, Hooks
from prova.harness.permissions import load_profiles
from prova.harness.verifiers import (
    AttacksWrittenBeforeStop,
    FilesListed,
    LintAfterEdit,
    ReproFailsBeforeStop,
    TestsBeforeStop,
)
from prova.mechanisms import builtin_mechanisms
from tests.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[2]
SECTIONS = ["Identity", "You MAY", "You MUST NOT", "Definition of done", "Output contract", "Escalate"]
SHARED = {"no_secret_reads", "no_network_tools", "no_network_in_commands", "no_secrets_in_logs"}


@dataclass(frozen=True)
class Role:
    name: str
    gate: Hook  # the stop hook its contract names
    only_requests: tuple[str, ...]
    done: tuple[str, ...]
    folder: str  # the only place it may write

    @property
    def contract(self) -> Path:
        return ROOT / "contract" / self.name / "contract.yaml"

    @property
    def agents(self) -> Path:
        return ROOT / "contract" / self.name / "AGENTS.md"

    def hooks(self) -> Hooks:
        return Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), self.gate))


ROLES = [
    Role(
        "repro",
        ReproFailsBeforeStop(),
        ("test_behaviour", "no_guessing"),
        ("repro_fails", "lint_checked", "report_submitted", "files_listed"),
        "repro/",
    ),
    Role(
        "adversary",
        AttacksWrittenBeforeStop(),
        ("no_credit_for_agreeing", "no_guessing"),
        ("attacks_written", "lint_checked", "report_submitted", "files_listed"),
        "attack/",
    ),
]


def real_context(settings: Settings, role: Role, **changes: object) -> VerifyContext:
    ctx = VerifyContext(
        profiles=load_profiles(ROOT / "permissions.yaml"),
        hooks=role.hooks(),
        tools=build_app(settings, llm=FakeLLM()).tools,
        project_root=ROOT,
    )
    return dataclasses.replace(ctx, **changes)


roles = pytest.mark.parametrize("role", ROLES, ids=lambda role: role.name)


# --- It holds -------------------------------------------------------------------------------------


@roles
def test_the_contract_holds_on_the_real_profile_and_hooks_of_the_role(settings: Settings, role: Role) -> None:
    report = check_contract(load_contract(role.contract), builtin_mechanisms(), real_context(settings, role))
    assert report.prompt_only == role.only_requests


@roles
def test_every_rule_that_can_be_enforced_is(role: Role) -> None:
    enforced = {r.id for r in load_contract(role.contract).must_not if r.enforced_by != "prompt_only"}
    assert enforced == {"no_code_edits", *SHARED}


@roles
def test_the_definition_of_done_is_the_one_of_the_role(role: Role) -> None:
    assert tuple(rule.id for rule in load_contract(role.contract).definition_of_done) == role.done


# --- It breaks when the configuration stops backing it --------------------------------------------


@roles
def test_a_profile_that_may_write_the_code_makes_the_contract_fail(settings: Settings, role: Role) -> None:
    profiles = load_profiles(ROOT / "permissions.yaml")
    loose = profiles[role.name].model_copy(update={"write_paths": ("**",)})
    ctx = real_context(settings, role, profiles={**profiles, role.name: loose})
    with pytest.raises(ContractError, match=r"no_code_edits.*lets .* be written"):
        check_contract(load_contract(role.contract), builtin_mechanisms(), ctx)


@roles
def test_a_run_with_the_gate_of_a_fix_run_fails_the_contract(settings: Settings, role: Role) -> None:
    hooks = Hooks(post=(LintAfterEdit(),), stop=(FilesListed(), TestsBeforeStop()))
    with pytest.raises(ContractError, match=role.done[0]):
        check_contract(
            load_contract(role.contract), builtin_mechanisms(), real_context(settings, role, hooks=hooks)
        )


# --- The two files say the same thing, and the model reads a short structured file ---------------


@roles
def test_every_rule_of_the_yaml_is_in_the_agents_file_word_for_word(role: Role) -> None:
    contract = load_contract(role.contract)
    agents = role.agents.read_text(encoding="utf-8")
    for rule in (*contract.must_not, *contract.definition_of_done):
        assert rule.text in agents, f"{rule.id}: its text is not in {role.agents}"


@roles
def test_every_escalation_and_output_field_is_explained_in_the_agents_file(role: Role) -> None:
    contract = load_contract(role.contract)
    agents = role.agents.read_text(encoding="utf-8")
    for name in (*contract.escalate_when, *contract.output_contract):
        assert f"`{name}`" in agents, name


@roles
def test_the_agents_file_has_every_section_in_order_and_says_where_it_writes(role: Role) -> None:
    text = role.agents.read_text(encoding="utf-8")
    assert re.findall(r"^## (.+)$", text, flags=re.MULTILINE) == SECTIONS
    assert role.folder in text
