"""Reviewers of a patch (M14): none, a self-review in words, and an adversary that may block only with proof.

A reviewer is a function from (task, diff, patched workspace, trajectory) to a Review. The adversary runs
the product in the `adversary` role on the patched code: it writes attack tests in attack/, and the harness,
not the model, reads the verdict from them. "block" only when an attack fails on the patched code for the
right reason (prova.agent.repro); "pass" otherwise, unreadable output included: no proof, no block. The
attacks are kept next to the trajectory with their verdict.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from bench.loop_agent import Product, open_session
from bench.repro_agent import keep_out_of_the_workspace, save_check
from bench.task import Task
from prova.agent.repro import ATTACK_ARGV, ATTACK_DIR, ReproCheck, classify_repro
from prova.config import Settings
from prova.errors import StructuredError
from prova.llm.client import LLMClient
from prova.llm.usage import UsageLog
from prova.prompts.render import render_user_context
from prova.prompts.tasks import task_prompt
from prova.structured import extract

Verdict = Literal["block", "pass"]
ADVERSARY_TASK = task_prompt("adversary", folder=ATTACK_DIR)
SELF_REVIEW_TASK = task_prompt("self_review")
ATTACK_TIMEOUT_S = 120.0


class SelfVerdict(BaseModel):
    """What a self-review answers. Words only: nothing is run."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    reason: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class Review:
    verdict: Verdict
    reason: str
    usage: UsageLog = field(default_factory=UsageLog)
    evidence: ReproCheck | None = None  # how the attack tests failed (the adversary only)


class Reviewer(Protocol):
    def __call__(self, task: Task, diff: str, workspace: Path, trajectory: Path) -> Review: ...


def review_prompt(task: Task, diff: str) -> str:
    """The issue and the patch, both data written by others, in one untrusted block."""
    return render_user_context(f"{task.issue}\n\nThe patch:\n```diff\n{diff}\n```")


@dataclass(frozen=True, slots=True)
class NoReviewer:
    """Lets every patch through: the baseline."""

    def __call__(self, task: Task, diff: str, workspace: Path, trajectory: Path) -> Review:
        return Review("pass", "no reviewer")


@dataclass(frozen=True, slots=True)
class SelfReviewer:
    """The same agent, with the system prompt of a fix run, asked to judge the patch: one call, no tools. A
    reply that cannot be read gives no verdict, which lets the patch through, and is still paid for."""

    llm: LLMClient
    settings: Settings
    system: str  # the system prompt of a fix run (ProductAgent.system)

    def __call__(self, task: Task, diff: str, workspace: Path, trajectory: Path) -> Review:
        used = UsageLog()
        user = f"{review_prompt(task, diff)}\n\n{SELF_REVIEW_TASK}"
        try:
            answer = extract(
                self.llm, SelfVerdict, system=self.system, user=user, settings=self.settings, usage=used
            )
        except StructuredError as e:
            return Review("pass", f"no verdict: the review could not be read ({e})", used)
        return Review(answer.verdict, answer.reason, used)


@dataclass(frozen=True, slots=True)
class AdversaryReviewer:
    adversary: Product  # the product in the `adversary` role
    seconds: Callable[[], float] = field(default=time.monotonic)

    def __call__(self, task: Task, diff: str, workspace: Path, trajectory: Path) -> Review:
        with open_session(self.adversary, task, workspace, trajectory, self.seconds) as session:
            try:
                session.attempt(self.adversary, f"{review_prompt(task, diff)}\n\n{ADVERSARY_TASK}")
                found = session.sandbox.run(ATTACK_ARGV, timeout_s=ATTACK_TIMEOUT_S, cwd="/work")
            finally:
                keep_out_of_the_workspace(workspace, trajectory, ATTACK_DIR)
        used = session.spend.used
        if found.timed_out:
            return Review("pass", "no proof: the attack tests did not finish", used)
        check = classify_repro(
            found.output, exit_code=found.exit_code, truncated=found.truncated, root="/work"
        )
        save_check(trajectory, ATTACK_DIR, check)
        if check.valid:
            return Review("block", f"an attack broke the patch: {check.reason}", used, check)
        return Review(
            "pass", f"no proof that the patch is broken ({check.kind}): {check.reason}", used, check
        )
