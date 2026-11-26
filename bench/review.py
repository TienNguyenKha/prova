"""Measure reviewers on good and bad patches (M14): what they catch, what they block by mistake, the cost.

  uv run python -m bench.review         # shows the plan and the most it can cost, runs nothing
  uv run python -m bench.review --reviewer self --reviewer adversary --seeds 3 --confirm

Each case of bench/review/cases.yaml is a patch of a task with a label that the hidden tests back. A reviewer
gets the patched code WITHOUT the hidden tests (they are the answer) and the diff, and says block or pass. One
line of result per review; a review that crashed is a line that says so, never a verdict. The summary gives,
for each reviewer, the bad patches it blocked, the good patches it blocked by mistake, and the cost of a
review, with the mean by seed and its SEM. Two warnings come with it: a reviewer that blocked nothing in
its last reviews (a rubber stamp), and the cases where self-review and the adversary disagree, which are the
ones a person should read.
"""

import argparse
import json
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.review_cases import CASES, TASKS, Case, load_cases, task_dir
from bench.reviewers import Reviewer
from bench.run import at_least_one, new_run_id
from bench.task import load_task
from bench.validate_tasks import apply_patch
from bench.workspace import prepare_workspace
from prova.agent.budget import Pricing
from prova.stats import mean_sem

BENCH = Path(__file__).resolve().parent
REVIEWERS = ("none", "self", "adversary")
SELF_REVIEW_CEILING_USD = 0.05  # one call: a bound on its prompt (an issue and a diff) and on max_tokens
RUBBER_STAMP_LAST = 10
# (the cases, seeds, the names of the reviewers) -> exit code
Execute = Callable[[list[Case], int, list[str]], int]


class ReviewRunError(Exception):
    """The patch of a case does not apply to its task."""


# -- one review ----------------------------------------------------------------------------------------


def review_case(  # noqa: PLR0913 - everything one review needs, named
    case: Case,
    name: str,
    reviewer: Reviewer,
    *,
    tasks: Path,
    results: Path,
    run_id: str,
    seed: int,
    pricing: Pricing | None,
    config: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Let `reviewer` judge the patch of `case` and append one line to `results`."""
    folder = task_dir(tasks, case.task)
    task = load_task(folder)
    diff = case.patch_file.read_text(encoding="utf-8")
    trajectory = results.parent / "trajectories" / f"{run_id}-{name}-{case.id}-s{seed}.jsonl"
    line: dict[str, Any] = {
        "run_id": run_id, "reviewer": name, "case": case.id, "task": case.task, "label": case.label,
        "kind": case.kind, "seed": seed, "config": dict(config or {}),
    }  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "patched"
        prepare_workspace(folder, workspace)  # repo/ only: never the hidden tests
        problem = apply_patch(case.patch_file, workspace)
        if problem is not None:
            raise ReviewRunError(f"{case.id}: the patch does not apply: {problem}")
        started = clock()
        try:
            review = reviewer(task, diff, workspace, trajectory)
        except Exception as e:  # noqa: BLE001 - whatever the reviewer did, the review still gets its line
            line |= {
                "verdict": None,
                "correct": None,
                "reason": "",
                "cost_usd": None,
                "error": f"{type(e).__name__}: {e}",
            }
        else:
            line |= {
                "verdict": review.verdict,
                "correct": (review.verdict == "block") == (case.label == "bad"),
                "reason": review.reason,
                "cost_usd": _cost(review.usage, pricing),
                "error": None,
                "evidence": None if review.evidence is None else review.evidence.kind,
            }
        line["seconds"] = clock() - started
    line["trajectory"] = f"trajectories/{trajectory.name}"
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def _cost(usage: Any, pricing: Pricing | None) -> float | None:
    if pricing is None:
        return None
    try:
        return pricing.cost(usage)
    except ValueError:
        return None  # a price that is not known is never 0


# -- the numbers ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class Summary:
    caught: tuple[int, int] = (0, 0)  # bad patches blocked, bad patches reviewed
    false_blocks: tuple[int, int] = (0, 0)  # good patches blocked, good patches reviewed
    crashed: int = 0
    caught_by_seed: list[float] = field(default_factory=list[float])
    false_blocks_by_seed: list[float] = field(default_factory=list[float])
    cost_per_review: float | None = None  # None when a cost is not known


def summarize(rows: list[dict[str, Any]]) -> dict[str, Summary]:
    out: dict[str, Summary] = {}
    for name in sorted({str(r["reviewer"]) for r in rows}):
        mine = [r for r in rows if r["reviewer"] == name]
        done = [r for r in mine if r["verdict"] is not None]
        summary = Summary(crashed=len(mine) - len(done))
        summary.caught = _count(done, "bad")
        summary.false_blocks = _count(done, "good")
        for seed in sorted({int(r["seed"]) for r in done}):
            of_seed = [r for r in done if r["seed"] == seed]
            summary.caught_by_seed.append(_rate(_count(of_seed, "bad")))
            summary.false_blocks_by_seed.append(_rate(_count(of_seed, "good")))
        costs = [r["cost_usd"] for r in done]
        if done and all(cost is not None for cost in costs):
            summary.cost_per_review = sum(costs) / len(done)
        out[name] = summary
    return out


def _count(rows: list[dict[str, Any]], label: str) -> tuple[int, int]:
    of_label = [r for r in rows if r["label"] == label]
    return sum(r["verdict"] == "block" for r in of_label), len(of_label)


def _rate(counted: tuple[int, int]) -> float:
    blocked, total = counted
    return blocked / total if total else 0.0


def rubber_stamps(rows: list[dict[str, Any]], *, last: int = RUBBER_STAMP_LAST) -> list[str]:
    """The reviewers that blocked nothing in their `last` reviews (all of them, if they did fewer)."""
    stamps: list[str] = []
    for name in sorted({str(r["reviewer"]) for r in rows} - {"none"}):
        recent = [r for r in rows if r["reviewer"] == name and r["verdict"] is not None][-last:]
        if recent and all(r["verdict"] == "pass" for r in recent):
            stamps.append(name)
    return stamps


def disagreements(rows: list[dict[str, Any]], one: str, other: str) -> list[tuple[str, int, str, str]]:
    """(case, seed, verdict of `one`, verdict of `other`) where both reviewed and said different things."""
    first = {(r["case"], r["seed"]): r["verdict"] for r in rows if r["reviewer"] == one and r["verdict"]}
    second = {(r["case"], r["seed"]): r["verdict"] for r in rows if r["reviewer"] == other and r["verdict"]}
    return sorted(
        (case, seed, first[(case, seed)], second[(case, seed)])
        for case, seed in first.keys() & second.keys()
        if first[(case, seed)] != second[(case, seed)]
    )


def render(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| reviewer | bad blocked (caught) | good blocked (by mistake) | crashed | $ / review |",
        "|---|---|---|---|---|",
    ]
    for name, s in summarize(rows).items():
        lines.append(
            f"| {name} | {_cell(s.caught, s.caught_by_seed)} "
            f"| {_cell(s.false_blocks, s.false_blocks_by_seed)} | {s.crashed} "
            f"| {'n/a' if s.cost_per_review is None else f'{s.cost_per_review:.4f}'} |"
        )
    stamps = rubber_stamps(rows)
    if stamps:
        lines.append(f"\nblocked nothing in its last {RUBBER_STAMP_LAST} reviews: {', '.join(stamps)}")
    both = disagreements(rows, "self", "adversary")
    if both:
        lines.append("\nself-review and adversary disagree (read these):")
        lines += [f"- {case} seed {seed}: self {a}, adversary {b}" for case, seed, a, b in both]
    return "\n".join(lines)


def _cell(counted: tuple[int, int], by_seed: list[float]) -> str:
    blocked, total = counted
    text = f"{blocked}/{total}"
    if len(by_seed) >= 2:  # noqa: PLR2004 - a spread needs two seeds
        mean, sem = mean_sem(by_seed)
        text += f" ({mean:.2f} ± {sem:.2f})"
    return text


# -- the command ---------------------------------------------------------------------------------------


def ceiling_usd(*, cases: int, seeds: int, reviewers: list[str], task_limit: float) -> float:
    """The most a run can cost: an adversary review spends at most the money limit of its task."""
    per_review = {"none": 0.0, "self": SELF_REVIEW_CEILING_USD, "adversary": task_limit}
    return sum(per_review[name] for name in reviewers) * cases * seeds


def main(argv: list[str], *, execute: Execute | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure reviewers on good and bad patches")
    parser.add_argument(
        "--reviewer", action="append", choices=REVIEWERS, default=[], help="default: all three"
    )
    parser.add_argument("--seeds", type=at_least_one, default=1)
    parser.add_argument("--confirm", action="store_true", help="really run: calls the API, costs money")
    args = parser.parse_args(argv)
    reviewers: list[str] = list(args.reviewer) or list(REVIEWERS)
    cases = load_cases(CASES, TASKS)
    limit = max(load_task(task_dir(TASKS, c.task)).budget.max_usd for c in cases)
    ceiling = ceiling_usd(cases=len(cases), seeds=args.seeds, reviewers=reviewers, task_limit=limit)
    good = sum(c.label == "good" for c in cases)
    print(  # noqa: T201
        f"{len(cases)} cases ({good} good, {len(cases) - good} bad) x {args.seeds} seed(s) x reviewers "
        f"{', '.join(reviewers)}\nthe most this can cost: ${ceiling:.2f}"
    )
    if not args.confirm:
        print("nothing was run. Add --confirm to run it.")  # noqa: T201
        return 0
    return (execute or _execute)(cases, args.seeds, reviewers)


def _execute(cases: list[Case], seeds: int, names: list[str]) -> int:  # needs the API and Docker
    from bench.pricing import load_price_table  # noqa: PLC0415
    from bench.product import make_agent  # noqa: PLC0415
    from bench.reviewers import AdversaryReviewer, NoReviewer, SelfReviewer  # noqa: PLC0415
    from prova.app import build_app  # noqa: PLC0415
    from prova.config import load_settings  # noqa: PLC0415
    from prova.harness.permissions import load_profiles  # noqa: PLC0415
    from prova.llm.client import make_client  # noqa: PLC0415
    from prova.prompts.tasks import task_prompt_hashes  # noqa: PLC0415
    from prova.sandbox.docker import DockerSandbox  # noqa: PLC0415

    settings = load_settings()
    root = settings.project_root
    pricing = load_price_table(BENCH / "pricing.yaml").pricing_for(settings.model)
    app = build_app(settings, llm=make_client(settings))
    profiles = load_profiles(root / "permissions.yaml")

    def product(role: str) -> Any:
        return make_agent(
            app=app, profiles=profiles, root=root, pricing=pricing, role=role,
            open_sandbox=lambda workspace: DockerSandbox(workspace), clock=lambda: datetime.now(UTC),
        )  # fmt: skip

    reviewers: dict[str, Reviewer] = {"none": NoReviewer()}
    if "self" in names:
        reviewers["self"] = SelfReviewer(app.llm, settings, system=product("fix").system)
    if "adversary" in names:
        reviewers["adversary"] = AdversaryReviewer(product("adversary"))
    run_id = new_run_id(datetime.now(UTC))
    results = BENCH / "results" / f"review-{run_id}.jsonl"
    prompts = {
        "none": {},
        "self": task_prompt_hashes("self_review"),
        "adversary": task_prompt_hashes("adversary"),
    }
    configs = {name: {"model": settings.model, "task_prompts": prompts[name]} for name in names}
    rows = [
        review_case(
            case,
            name,
            reviewers[name],
            tasks=TASKS,
            results=results,
            run_id=run_id,
            seed=seed,
            pricing=pricing,
            config=configs[name],
        )
        for seed in range(seeds)
        for case in cases
        for name in names
    ]
    print(f"results: {results}\n\n{render(rows)}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
