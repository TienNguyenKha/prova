import json
import subprocess
from datetime import datetime, timezone


def load_processed() -> set[int]:
    """Return set of issue numbers processed today."""
    today = datetime.now(timezone.utc).date().isoformat()
    if not PROCESSED_LOG.exists():
        return set()
    log = json.loads(PROCESSED_LOG.read_text())
    return {entry["number"] for entry in log.get(today, [])}


def mark_processed(issue_number: int, result: str):
    """Record that we processed this issue today."""
    today = datetime.now(timezone.utc).date().isoformat()
    log = json.loads(PROCESSED_LOG.read_text()) if PROCESSED_LOG.exists() else {}
    log.setdefault(today, []).append({
        "number": issue_number,
        "result": result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    PROCESSED_LOG.write_text(json.dumps(log, indent=2))


def post_plan_comment(repo: str, issue_number: int, plan: list[str]):
    """Post Atlas's implementation plan as a GitHub issue comment."""
    plan_text = "\n".join(f"{i+1}. {task}" for i, task in enumerate(plan))
    body = (
        "## Atlas Implementation Plan\n\n"
        f"Atlas v0.20 analyzed this issue and proposes:\n\n"
        f"{plan_text}\n\n"
        "---\n"
        "*Reply with `/atlas approve` to run the full implementation.*"
    )
    subprocess.run(
        ["gh", "issue", "comment", str(issue_number),
         "--repo", repo, "--body", body],
        capture_output=True,
    )


def nightly_sweep(repo: str, max_issues: int = 10, dry_run: bool = False):
    """Process up to max_issues open issues tagged atlas-ready."""
    issues = fetch_open_issues(repo)
    processed = load_processed()

    pending = [i for i in issues if i["number"] not in processed]
    pending.sort(key=priority_score)

    for issue in pending[:max_issues]:
        number = issue["number"]
        try:
            state = {"repo": repo, "issue_number": number, "issue": issue}
            updated = planner_node(state)
            plan = updated.get("plan", [])

            if plan and not dry_run:
                post_plan_comment(repo, number, plan)

            mark_processed(number, "planned")
        except Exception as e:
            print(f"Error processing issue #{number}: {e}")
            mark_processed(number, f"error: {e}")
