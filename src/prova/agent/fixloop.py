"""The loop that fixes, tests and fixes again, and gives up in the right way.

The agent, the way to read the files and the check are handed in, so the loop is tested with fakes. Each
attempt is told the goal and a few lines of feedback built from facts (see prova.agent.feedback), never the
whole story of the earlier attempts. The loop is done only when the check exits with 0. A check that cannot
show anything (it found no test, or its output cannot be read) is never a pass: no test is `unverifiable`.

When it gives up it says why and leaves a record: a state file, and one line in escalations.md.
"""

import json
import shlex
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from prova.agent.budget import Pricing
from prova.agent.feedback import Lesson, make_lesson, structured_feedback
from prova.harness.redact import Redactor
from prova.llm.usage import UsageLog
from prova.prompts.render import render_user_context
from prova.prompts.tasks import task_prompt

_EXIT_PASSED = 0
_EXIT_NO_TESTS = 5  # pytest found nothing to run
_STUCK_AFTER = 2  # the same failure this many attempts in a row
_CUT = 80

Reason = Literal["max_attempts", "budget", "stuck", "error", "unverifiable", "open_questions"]


class Goal(BaseModel):
    """What the loop is for. `termination_check` is a command a machine can run: a goal that cannot be checked
    by one is not a goal yet, and `open_questions` are what stands between the issue and such a goal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1)
    termination_check: list[str] = Field(min_length=1)
    max_attempts: int = Field(default=6, ge=1)
    max_usd: float = Field(default=1.0, gt=0)
    escalation: Literal["stop_and_report", "ask_human"]
    open_questions: list[str] = []


@dataclass(frozen=True, slots=True)
class AttemptRun:
    """What one run of the agent says about itself. Whether it fixed anything is for the check to say."""

    status: str
    detail: str
    usage: UsageLog


@dataclass(frozen=True, slots=True)
class Verification:
    exit_code: int
    output: str
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class LoopResult:
    status: Literal["done", "escalated"]
    reason: Reason | None
    attempts: int  # how many attempts were started, the one that crashed included
    lessons: tuple[Lesson, ...]
    cost_usd: float | None  # None when it cannot be known
    detail: str = ""
    question: str | None = None  # only when the goal says `ask_human` and the loop gave up


def run_fix_loop(  # noqa: PLR0913 - everything the loop needs, named
    goal: Goal,
    *,
    attempt: Callable[[str], AttemptRun],
    read_files: Callable[[], dict[str, str]],
    verify: Callable[[], Verification],
    pricing: Pricing,
    escalations_md: Path,
    state_file: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    redactor: Redactor | None = None,
) -> LoopResult:
    def finish(
        status: Literal["done", "escalated"], reason: Reason | None, attempts: int, detail: str = ""
    ) -> LoopResult:
        question = _question(goal, reason, detail) if status == "escalated" else None
        result = LoopResult(status, reason, attempts, tuple(lessons), cost, detail, question)
        _record(goal, result, state_file, escalations_md, now(), redactor or Redactor())
        return result

    lessons: list[Lesson] = []
    spent = 0.0
    cost: float | None = 0.0
    if goal.open_questions:
        return finish("escalated", "open_questions", 0, "; ".join(goal.open_questions))

    # The check is run once before anything is spent: one that finds no test, or a goal already met, needs no
    # attempt. A check that ran out of time here decides nothing.
    try:
        first = verify()
    except Exception as e:  # noqa: BLE001 - a check that cannot run is not a result; the record is kept
        return finish("escalated", "error", 0, f"the check could not run: {type(e).__name__}: {e}")
    if not first.timed_out and first.exit_code == _EXIT_NO_TESTS:
        return finish("escalated", "unverifiable", 0, "the check found no test to run, before any change")
    if not first.timed_out and first.exit_code == _EXIT_PASSED:
        return finish("done", None, 0, "the goal was already met before any attempt")

    feedback: str | None = None
    for number in range(1, goal.max_attempts + 1):
        before = read_files()
        try:
            run = attempt(_prompt(goal, feedback))
        except Exception as e:  # noqa: BLE001 - whatever the agent did, the lessons so far are kept
            return finish("escalated", "error", number, f"{type(e).__name__}: {e}")
        after = read_files()
        changes = {p: (before.get(p), after.get(p)) for p in sorted({*before, *after})}
        try:
            spent += pricing.cost(run.usage)
            cost, why_unknown = spent, ""
        except ValueError as e:  # a price for what was used is not known: the money limit cannot be kept
            cost, why_unknown = None, str(e)

        try:
            check = verify()
        except Exception as e:  # noqa: BLE001 - a check that cannot run is not a result; the record is kept
            return finish("escalated", "error", number, f"the check could not run: {type(e).__name__}: {e}")
        if check.exit_code == _EXIT_PASSED and not check.timed_out:
            return finish("done", None, number)
        if check.exit_code == _EXIT_NO_TESTS and not check.timed_out:
            return finish("escalated", "unverifiable", number, "the check found no test to run")

        # what a check that did not finish printed is not trusted
        shown = "" if check.timed_out else check.output
        lessons.append(make_lesson(number, changes, shown))
        if cost is None:
            return finish("escalated", "budget", number, f"the cost cannot be known: {why_unknown}")
        if cost > goal.max_usd:
            return finish(
                "escalated", "budget", number, f"${cost:.4f} spent, over the limit of ${goal.max_usd:.4f}"
            )
        if run.status == "budget":  # the budget is the loop's: what one attempt used up is gone for the next
            return finish("escalated", "budget", number, f"the attempt ran out of budget: {run.detail}")
        if _is_stuck(lessons):
            return finish(
                "escalated",
                "stuck",
                number,
                f"the same failure came back in attempts {number - 1} and {number}",
            )
        feedback = structured_feedback(shown, lessons[:-1], latest=lessons[-1])
    return finish(
        "escalated", "max_attempts", goal.max_attempts, f"{goal.max_attempts} attempts did not meet the goal"
    )


# -- the parts ----------------------------------------------------------------------------------------


def _prompt(goal: Goal, feedback: str | None) -> str:
    parts = [
        render_user_context(goal.statement),  # the goal comes from an issue: it is data from somebody else
        task_prompt("fix_goal", check=shlex.join(goal.termination_check)),
    ]
    if feedback:
        parts.append(task_prompt("fix_feedback", feedback=feedback))
    return "\n\n".join(parts)


def _is_stuck(lessons: list[Lesson]) -> bool:
    """The last attempts failed in the same way. A failure that could not be read never equals another."""
    if len(lessons) < _STUCK_AFTER:
        return False
    latest = lessons[-1].fingerprint
    return latest is not None and all(lesson.fingerprint == latest for lesson in lessons[-_STUCK_AFTER:])


def _question(goal: Goal, reason: Reason | None, detail: str) -> str | None:
    if goal.escalation != "ask_human":
        return None
    if reason == "open_questions":
        return f"I need an answer before I start: {detail}"
    return f"The loop stopped ({reason}): {detail}. How should it go on?"


# -- the record ---------------------------------------------------------------------------------------


def _record(
    goal: Goal, result: LoopResult, state_file: Path, escalations_md: Path, at: datetime, redactor: Redactor
) -> None:
    state = {
        "schema_version": 1,
        "updated": at.isoformat(),
        "status": result.status,
        "reason": result.reason,
        "detail": result.detail,
        "attempts": result.attempts,
        "cost_usd": result.cost_usd,
        "goal": goal.model_dump(mode="json"),
        "lessons": [asdict(lesson) for lesson in result.lessons],
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(redactor.redact_data(state), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if result.status != "escalated":
        return
    statement = " ".join(goal.statement.split())
    statement = statement if len(statement) <= _CUT else f"{statement[:_CUT]}…"
    line = (
        f"- {at:%Y-%m-%d %H:%M} | {result.reason} | {statement} | attempts {result.attempts} | "
        f"state: {state_file}\n"
    )
    escalations_md.parent.mkdir(parents=True, exist_ok=True)
    with escalations_md.open("a", encoding="utf-8") as f:
        f.write(redactor.redact(line))
