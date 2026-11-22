def run_loop(repo: Path, goal: str) -> dict:
    lessons: list[str] = []
    experience = retrieve_experience(goal)
    last_fp, stall_count, patch_summary = None, 0, ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        done, evidence = verify(repo)
        if done:
            if lessons:
                path = distill_trajectory(goal, lessons, patch_summary)
                print(f"Trajectory distilled -> {path}")
            return {"status": "success", "attempts": attempt}

        fp = failure_fingerprint(evidence)
        n_failures = evidence.count("FAILED")
        print(f"Attempt {attempt}: {n_failures} failures [fp: {fp}]")

        stall_count = stall_count + 1 if fp == last_fp else 0
        last_fp = fp
        if stall_count >= STALL_LIMIT:
            return escalate(goal, lessons, f"same failure {stall_count + 1}x ({fp})")

        patch_summary = generate_and_apply(repo, goal, evidence, lessons, experience)
        lessons.append(summarize_failure(attempt, evidence, patch_summary))
        print("  lesson recorded")

    done, _ = verify(repo)
    if done:
        return {"status": "success", "attempts": MAX_ATTEMPTS}
    return escalate(goal, lessons, "max attempts exhausted")
