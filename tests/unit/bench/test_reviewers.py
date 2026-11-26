"""Reviewers of a patch (M14): none, and an adversary that may block only with proof.

The adversary writes attack tests; the harness, not the model, reads the verdict from them: "block" only when
an attack fails on the patched code for the right reason (see prova.agent.repro), "pass" otherwise, unreadable
output included. No proof, no block. Fakes stand in for the model and the sandbox.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bench.reviewers import (
    ADVERSARY_TASK,
    SELF_REVIEW_TASK,
    AdversaryReviewer,
    NoReviewer,
    Review,
    SelfReviewer,
)
from bench.task import Task, load_task
from prova.agent.budget import Budget, Pricing
from prova.agent.loop import AgentResult
from prova.agent.repro import ATTACK_ARGV
from prova.config import Settings
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.llm.usage import UsageLog, UsageRecord
from prova.sandbox.base import ExecResult, Sandbox
from tests.fakes import FakeLLM, FakeSandbox, text_message
from tests.unit.agent.test_repro import ASSERTION, IN_THE_TEST, SUMMARY_LINES, run
from tests.unit.bench.test_task import make_task

PRICING = Pricing(input_per_mtok=2.0, output_per_mtok=10.0)
BROKE = ExecResult(1, run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]), "", False, 0.1, False)
HELD = ExecResult(0, "2 passed in 0.01s\n", "", False, 0.1, False)
BROKEN_ATTACK = ExecResult(1, run(IN_THE_TEST, summary=[SUMMARY_LINES["typo"]]), "", False, 0.1, False)
DIFF = "--- a/lib.py\n+++ b/lib.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


class Adversary:
    """Stands in for the product in the adversary role: it writes an attack test and reports its usage."""

    def __init__(self, sandbox: FakeSandbox) -> None:
        self.pricing = PRICING
        self.config: dict[str, Any] = {"profile": "adversary", "prompt_hash": "a" * 64}
        self.sandbox = sandbox
        self.seen: list[dict[str, Any]] = []

    def clock(self) -> datetime:
        return datetime(2026, 12, 1, tzinfo=UTC)

    @contextmanager
    def open_sandbox(self, workspace: Path) -> Iterator[Sandbox]:
        yield self.sandbox

    def attempt(  # noqa: PLR0913 - the same as the product's
        self, prompt: str, *, sandbox: Sandbox, workspace: Path, writer: TrajectoryWriter, budget: Budget,
        redactor: Redactor,
    ) -> AgentResult:  # fmt: skip
        self.seen.append({"prompt": prompt, "workspace": workspace})
        (workspace / "attack").mkdir(exist_ok=True)
        (workspace / "attack" / "test_break.py").write_text("x\n", encoding="utf-8")
        log = UsageLog()
        log.add(UsageRecord(turn=1, input_tokens=10_000, output_tokens=1_000))
        return AgentResult("done", "ok", 1, log, ())


def setting(tmp_path: Path, found: ExecResult) -> tuple[Task, Path, Path, Adversary]:
    task = load_task(make_task(tmp_path / "tasks"))
    workspace = tmp_path / "patched"
    workspace.mkdir()
    trajectory = tmp_path / "out" / "trajectories" / "review-c1-s0.jsonl"
    return task, workspace, trajectory, Adversary(FakeSandbox(found))


def test_no_reviewer_lets_everything_through_and_costs_nothing(tmp_path: Path) -> None:
    task, workspace, trajectory, _ = setting(tmp_path, HELD)
    review = NoReviewer()(task, DIFF, workspace, trajectory)
    assert review.verdict == "pass" and review.usage.records == ()


def test_an_attack_that_fails_for_the_right_reason_blocks_the_patch(tmp_path: Path) -> None:
    task, workspace, trajectory, adversary = setting(tmp_path, BROKE)
    review = AdversaryReviewer(adversary)(task, DIFF, workspace, trajectory)  # type: ignore[arg-type]
    assert review.verdict == "block" and review.evidence is not None and review.evidence.kind == "assertion"
    assert "test_add" in review.reason
    assert adversary.sandbox.calls[0]["argv"] == ATTACK_ARGV
    assert len(review.usage.records) == 1


def test_attacks_that_all_pass_let_the_patch_through(tmp_path: Path) -> None:
    task, workspace, trajectory, adversary = setting(tmp_path, HELD)
    review = AdversaryReviewer(adversary)(task, DIFF, workspace, trajectory)  # type: ignore[arg-type]
    assert review.verdict == "pass"


def test_an_attack_that_breaks_on_its_own_mistake_is_no_proof_so_it_does_not_block(tmp_path: Path) -> None:
    task, workspace, trajectory, adversary = setting(tmp_path, BROKEN_ATTACK)
    review = AdversaryReviewer(adversary)(task, DIFF, workspace, trajectory)  # type: ignore[arg-type]
    assert review.verdict == "pass" and "no proof" in review.reason
    assert review.evidence is not None and review.evidence.kind == "exception_in_test"


def test_attacks_that_were_cut_or_did_not_finish_are_no_proof(tmp_path: Path) -> None:
    for name, found in (
        ("cut", ExecResult(1, BROKE.stdout, "", False, 0.1, True)),
        ("late", ExecResult(-1, "", "", True, 120.0, False)),
    ):
        task, workspace, trajectory, adversary = setting(tmp_path / name, found)
        review = AdversaryReviewer(adversary)(task, DIFF, workspace, trajectory)  # type: ignore[arg-type]
        assert review.verdict == "pass" and "no proof" in review.reason
        assert ("did not finish" in review.reason) is (name == "late")


def test_the_adversary_is_told_the_issue_and_the_patch_as_data(tmp_path: Path) -> None:
    task, workspace, trajectory, adversary = setting(tmp_path, HELD)
    AdversaryReviewer(adversary)(task, DIFF, workspace, trajectory)  # type: ignore[arg-type]
    prompt = adversary.seen[0]["prompt"]
    assert prompt.startswith("<untrusted_issue>") and task.issue in prompt and "+x = 2" in prompt
    assert prompt.index("+x = 2") < prompt.index("</untrusted_issue>") < prompt.index(ADVERSARY_TASK)


def test_the_attacks_are_kept_next_to_the_trajectory_with_their_verdict(tmp_path: Path) -> None:
    task, workspace, trajectory, adversary = setting(tmp_path, BROKE)
    AdversaryReviewer(adversary)(task, DIFF, workspace, trajectory)  # type: ignore[arg-type]
    assert not (workspace / "attack").exists()
    assert (trajectory.with_name("review-c1-s0.attack") / "test_break.py").is_file()
    kept = json.loads(trajectory.with_name("review-c1-s0.attack.json").read_text(encoding="utf-8"))
    assert kept["valid"] is True and kept["kind"] == "assertion"


# --- self-review: the same agent asked to judge, with words only ------------------------------------


def self_review(tmp_path: Path, *replies: Any, settings: Settings) -> tuple[Review, FakeLLM]:
    task, workspace, trajectory, _ = setting(tmp_path, HELD)
    llm = FakeLLM(list(replies))
    review = SelfReviewer(llm, settings, system="the system prompt of a fix run")(
        task, DIFF, workspace, trajectory
    )
    return review, llm


def test_a_self_review_asks_with_the_fix_system_prompt_and_the_issue_and_patch_as_data(
    tmp_path: Path, settings: Settings
) -> None:
    review, llm = self_review(
        tmp_path, text_message('{"verdict": "pass", "reason": "looks right"}'), settings=settings
    )
    assert review.verdict == "pass" and review.reason == "looks right" and review.evidence is None
    (call,) = llm.messages.calls
    user: Any = call.messages[0]["content"]
    assert call.system == "the system prompt of a fix run"
    assert user.startswith("<untrusted_issue>") and "+x = 2" in user and SELF_REVIEW_TASK in user


def test_a_self_review_may_block_with_words(tmp_path: Path, settings: Settings) -> None:
    reply = text_message('{"verdict": "block", "reason": "misses the other case"}', usage=(1000, 100))
    review, _ = self_review(tmp_path, reply, settings=settings)
    assert review.verdict == "block" and review.reason == "misses the other case"
    assert review.usage.total_input_tokens == 1000


@pytest.mark.parametrize("text", ["not json", '{"verdict": "maybe", "reason": "x"}', '{"verdict": "block"}'])
def test_a_self_review_that_cannot_be_read_gives_no_verdict_and_is_still_paid_for(
    tmp_path: Path, settings: Settings, text: str
) -> None:
    review, _ = self_review(tmp_path, text_message(text, usage=(1000, 100)), settings=settings)
    assert review.verdict == "pass" and review.reason.startswith("no verdict")
    assert review.usage.total_input_tokens == 1000
