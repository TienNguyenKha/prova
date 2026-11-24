"""The contract of the agent: its rules, written so that a machine can check them.

Two files go together. `AGENTS.md` is what the model reads. `contract.yaml` is what code reads: the same
rules, each tied to the mechanism that enforces it. At start-up every mechanism is asked to prove that it
works on the configuration this run really has, and a run whose contract does not hold does not start.

A rule that nothing enforces and only the model is asked to follow is written `prompt_only`. That is
allowed, and it is listed apart: it is a request, not a rule, and the report says so.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from prova.errors import ConfigError, ContractError
from prova.harness.hooks import Hooks
from prova.harness.permissions import Profile
from prova.tools.registry import ToolRegistry
from prova.validation import describe_errors

PROMPT_ONLY = "prompt_only"
OUTPUT_FIELDS = ("files_changed_with_reason", "test_command", "assumptions")
_ID = re.compile(r"[a-z][a-z0-9_]{0,39}")
_FORBID = ConfigDict(extra="forbid")


def _check_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError(f"id {value!r} is not allowed: lowercase letters, digits, _, starting with a letter")
    return value


def _frozen_params(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@pydantic_dataclass(frozen=True, config=_FORBID)
class MustNot:
    id: str
    text: str = Field(min_length=1, max_length=300)  # what the model is told
    enforced_by: str = Field(min_length=1)  # the mechanism that makes it true, or `prompt_only`
    params: Mapping[str, Any] = Field(default_factory=dict[str, Any])  # what the mechanism tests itself with

    _id = field_validator("id")(_check_id)
    _params = field_validator("params")(_frozen_params)


@pydantic_dataclass(frozen=True, config=_FORBID)
class DoneCheck:
    id: str
    check: str = Field(min_length=1)  # the mechanism that checks it
    text: str = Field(default="", max_length=300)
    params: Mapping[str, Any] = Field(default_factory=dict[str, Any])

    _id = field_validator("id")(_check_id)
    _params = field_validator("params")(_frozen_params)


@pydantic_dataclass(frozen=True, config=_FORBID)
class Contract:
    version: Literal[1]
    must_not: tuple[MustNot, ...] = ()
    definition_of_done: tuple[DoneCheck, ...] = ()
    output_contract: tuple[str, ...] = ()  # what the final answer of the agent must contain
    escalate_when: tuple[str, ...] = ()  # when the agent must stop and ask a person

    @field_validator("output_contract")
    @classmethod
    def _known_output_fields(cls, fields: tuple[str, ...]) -> tuple[str, ...]:
        for name in fields:
            if name not in OUTPUT_FIELDS:
                raise ValueError(f"unknown output field {name!r}; known: {', '.join(OUTPUT_FIELDS)}")
        return fields

    @field_validator("escalate_when")
    @classmethod
    def _escalation_names(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        for name in names:
            _check_id(name)
        return names

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "Contract":
        seen: set[str] = set()
        for rule in (*self.must_not, *self.definition_of_done):
            if rule.id in seen:
                raise ValueError(f"the id {rule.id!r} is used twice")
            seen.add(rule.id)
        return self


def load_contract(path: Path) -> Contract:
    """Read contract.yaml. Anything wrong with it is a ConfigError that names the file and the problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read the contract {path}: {e.strerror or e}") from e
    try:
        raw: Any = yaml.safe_load(text)  # safe_load: a YAML file must never construct objects or run code
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML ({type(e).__name__}); nothing was loaded") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping")
    try:
        return Contract(**raw)  # pyright: ignore[reportUnknownArgumentType]
    except (ValidationError, TypeError) as e:
        detail = describe_errors(e) if isinstance(e, ValidationError) else str(e)
        raise ConfigError(f"{path} is not a valid contract: {detail}") from e


@dataclass(frozen=True, slots=True)
class VerifyContext:
    """What a mechanism may look at to prove it works: the configuration of THIS run, not an imagined one."""

    profiles: Mapping[str, Profile]
    hooks: Hooks
    tools: ToolRegistry
    project_root: Path


# A mechanism returns None when it holds, or a sentence saying how it does not.
Mechanism = Callable[[VerifyContext, Mapping[str, Any]], str | None]


@dataclass(frozen=True, slots=True)
class RuleStatus:
    rule: str
    kind: Literal["must_not", "done"]
    mechanism: str
    status: Literal["enforced", "prompt_only"]


@dataclass(frozen=True, slots=True)
class ContractReport:
    rules: tuple[RuleStatus, ...]

    @property
    def prompt_only(self) -> tuple[str, ...]:
        """The rules that are only requests to the model."""
        return tuple(r.rule for r in self.rules if r.status == "prompt_only")


def check_contract(
    contract: Contract, mechanisms: Mapping[str, Mechanism], ctx: VerifyContext
) -> ContractReport:
    """Ask the mechanism behind every rule to prove it holds. Raises ContractError listing EVERY rule that
    does not hold (no such mechanism, it says no, or it crashes); a crash counts as no."""
    statuses: list[RuleStatus] = []
    problems: list[str] = []
    rules: list[tuple[Literal["must_not", "done"], str, str, Mapping[str, Any]]] = [
        *(("must_not", r.id, r.enforced_by, r.params) for r in contract.must_not),
        *(("done", r.id, r.check, r.params) for r in contract.definition_of_done),
    ]
    for kind, rule, name, params in rules:
        if name == PROMPT_ONLY:
            statuses.append(RuleStatus(rule, kind, name, "prompt_only"))
            continue
        mechanism = mechanisms.get(name)
        if mechanism is None:
            problems.append(f"rule {rule!r} is tied to {name!r}: no such mechanism")
            continue
        try:
            problem = mechanism(ctx, params)
        except Exception as e:  # noqa: BLE001 - a mechanism that crashes has not proved anything
            problems.append(f"rule {rule!r} is tied to {name!r}: the check crashed ({type(e).__name__}: {e})")
            continue
        if problem is not None:
            problems.append(f"rule {rule!r} is tied to {name!r}: {problem}")
            continue
        statuses.append(RuleStatus(rule, kind, name, "enforced"))
    if problems:
        raise ContractError("the contract does not hold:\n- " + "\n- ".join(problems))
    return ContractReport(tuple(statuses))
