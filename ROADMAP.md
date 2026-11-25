# ROADMAP

Source of truth for *what is happening now*. The long-term plan lives in `PLAN.md` and `plans/`.
Update this file at the end of every working session.

## Implemented

- **M0a** Repo foundation: `uv` environment and tool config, `errors.py`, `config.py`, `registry.py`,
  `llm/client.py`, `app.py`, `tests/` (network block, fakes, 40 unit tests), `AGENTS.md`, `grill-me` command.
  `ruff`, `pyright` (strict) and `pytest` are green.
- **M0** One real API call: `experiments/act1/m0_first_call.py`, `m0_variants.py`, `m0_count_tokens.py`,
  `m0_errors.py`. Error table and `stop_reason` notes are in `LEARNINGS.md`.
- **M1** Multi-turn chat and streaming: `prova/llm/usage.py` (`UsageRecord`, `UsageLog`) with unit tests,
  `m1_chat.py`, `m1_plot.py`, `m1_stream.py`. Chart and TTFT table are in `LEARNINGS.md`.
  Step 6 (stream with a tool) moved to M2.
- **M2** First hand-written tool, 25 unit tests, `m2_break_rules.py`, `m2_stream_tool.py`. Rules table and answers
  in `LEARNINGS.md`. (The M2 prototypes of the loop and the tool runner were replaced in M4; `m2_tool_loop.py` deleted.)
- **M3** Structured output: `prova/structured.py` (`extract`, `check_stop_reason`, `parse_structured`,
  `BugClassification`), `StructuredError` + `StructuredErrorKind`, `prova/stats.py` (`mean_sem`), 30 unit tests,
  20 snippets in `experiments/act1/m3_cases/`, and four hand-run scripts. Both comparison tables are in `LEARNINGS.md`.
- **M4** Read-only ReAct loop: `agent/loop.py` (`run_agent`, all `stop_reason` cases), `tools/` (`Tool`, `ToolResult`,
  `ToolRegistry`, `read_file`, `list_dir`, `search_code`), `AnthropicClient` (SDK errors become `LLMError`),
  permissions enforced at dispatch, `build_app` registers and freezes the tools. 237 unit tests, and one real run
  (`experiments/act2/m4_ask_repo.py`). Trace, token table and answer in `LEARNINGS.md`.
- **M5** Workspace, permission profiles, trajectory log: `harness/` (`patterns`, `permissions`, `redact`,
  `trajectory`), `permissions.yaml` (`review`, `fix`), deny-list in `resolve_inside`, `ToolRegistry.is_denied`,
  secrets redacted in tool results and in the log, `run_agent(trajectory=, redactor=)`. 395 unit tests, and one real
  run with a fake `.env` whose trajectory reads with `jq` (`m4_ask_repo.py --bait`). Entry in `LEARNINGS.md`.
- **M5b** `tool_runner` comparison (`experiments/act2/m5_tool_runner.py`, one run): same tools, SDK loop; the fake
  key leaked into the answer because that loop has no redaction. Entry in `LEARNINGS.md`.
- **M6** Tools that change code: `read_file` numbers lines, `edit_file` and `create_file` (`tools/edit.py`),
  `SnapshotStore` (`harness/snapshots.py`), `RunContext.write` and `.snapshots`, four error codes for why an edit
  did not apply. 466 unit tests, fixture t001 and `experiments/act2/m6_fix_t001.py`: 4 real runs fixed it (1 Sonnet,
  3 Haiku), 0 of 4 edits failed, rollback not yet seen in a real run. Entry in `LEARNINGS.md`.
- **M7** Sandbox: `Sandbox` Protocol and `ExecResult`, `DockerSandbox` (no network, no root, read-only root file
  system, limits on CPU, memory and processes), `run_tests` tool (permission `exec`), pinned image in
  `docker/sandbox/`. 526 unit tests and 18 Docker tests; the plan's table ran for real. The agent fixed t001 and
  ran the tests itself in the sandbox (one real run). Entry in `LEARNINGS.md`.
- **M8** Hooks: `harness/hooks.py` (pre, post and stop hooks; a hook can only tighten), `harness/verifiers.py`
  (`LintAfterEdit`, `TestsBeforeStop`, both in the sandbox), `ToolResult.notes`, the `hook_decision` event, the run
  status `unverified`. 607 unit tests; six real runs, none refused by the stop hook, so the refusal path is proven
  for the mechanism only. Entry in `LEARNINGS.md`. Also fixed: `run_tests` no longer leaves `.pytest_cache`.
- **M8b** MCP client: `prova/mcp/` (`mcp.yaml` config, tool checks and fingerprint, `McpConnection` on its own
  thread, `McpToolAdapter`, `open_mcp_servers`), `build_app(extra_tools=...)`, `mcp.yaml` and a demo server.
  729 unit tests; one real run where the agent used `mcp__knowledge__search_knowledge`. The server runs on the
  host (ours only); nothing was tried against a hostile server. Entry in `LEARNINGS.md`.
- **M9** Contract: `contract/contract.yaml` + `contract/AGENTS.md`, `prova/contract.py` (`check_contract` at start-up),
  `prova/mechanisms.py` (probes on the real configuration), profile `write_deny_paths`, the `submit_result` tool, `Report`,
  `SnapshotStore.changed()`, the `FilesListed` stop hook. 874 unit tests; real runs: normal, and two where the task
  asks to change the tests (with and without the contract text: only the permissions stopped it in the second).
  Table "rule -> mechanism" in `LEARNINGS.md`, with the two rules that are only requests.
- **M9b** Prompt as code: `prova/prompts/` (`render_system` builds the system prompt from layers and only the tools the run is offered; `render_user_context` puts the task in `<untrusted_issue>`), `TaskEvent.prompt_hash`, the M4/M6/M8b scripts use it. 907 unit tests; real runs with two prompts gave two hashes. Entry in `LEARNINGS.md`.
- **Coverage floor of the tools:** `[tool.coverage]` in `pyproject.toml` (`source = src/prova/tools`, `fail_under = 100`); `uv run pytest --cov` exits 1 below it. `tools/` is at 100% (592 statements); 8 tests added for the branches that were not run. 916 unit tests. There is no CI yet (M10): until then the check is run by hand.
- **M10 Benchmark v0** Three tasks (`t001`, `t002`, `t006`) that pass the cross-check, `bench/task.py`, `validate_tasks.py`, price table (`bench/pricing.yaml`, `pricing.py`), budget of a run (`prova/agent/budget.py`), workspace, violations, grading, `runner.py`, `product.py`, `report.py`, and `python -m bench.run` (shows the plan and the ceiling; `--confirm` runs). 1161 unit tests, 26 Docker tests. First real run: 2 of 3 resolved, $0.20, 1 seed (`bench/results/2026-11-24T13-55_8979c5.jsonl`). Entries in `LEARNINGS.md`.
- **M12 Record/replay and trajectory viewer** `prova/llm/cassette.py` (`RecordingClient`, `ReplayClient`, durations of tools left out of the request hash), `bench.run --record`, `bench/cassettes/dev.jsonl` and a Docker test that replays the three tasks (same chain of tool calls, same grades, no API). 1212 unit tests, 30 Docker tests. `bench/view.py` shows a trajectory turn by turn (times from timestamps, cost from the price table, control characters removed). The scheduled CI gate (step 5) is in the backlog. M12b of the plan (comparison with Claude Code) is a different milestone. Entry in `LEARNINGS.md`.
- **M13 (in progress, core done)** `prova/agent/fingerprint.py`, `prova/agent/feedback.py`, `prova/agent/fixloop.py`: fingerprint of a failure, a few lines of feedback from facts, and the loop with its stop rules (done, stuck, attempts, money, unverifiable, crash, open questions) and its record (state file, `escalations.md`). 1300 unit tests; nothing of it has run with a real agent yet. Entry in `LEARNINGS.md`.
- **Three tasks with a visible failing test** for M13: `t009_fail_open_review` (a reviewer that cannot be reached or a reply that cannot be read approves), `t011_nightly_reminder` (a nightly job posts its reminder every night and on active issues), `t014_command_allowlist` (only the first word is checked, the shell runs the rest). Each has a test in `repo/tests/` the agent can run, and stricter hidden tests; all pass the cross-check. A fix of only the visible symptom fails 10 (t009), 4 (t011) and 17 (t014) hidden tests. The Docker replay test now runs only the three recorded tasks. 36 Docker tests. Entry in `LEARNINGS.md`.
- **M13 loop as a benchmark agent** `prova/agent/workspace_check.py` (`read_text_files` without following links, `run_check` in the sandbox), `bench/loop_agent.py` (`LoopAgent`: one sandbox and one trajectory for the loop, the task's budget shared by the attempts, usage added up), `ProductAgent.attempt`; the loop checks once before the first attempt, ends on an error of the check with its record, and stops when an attempt ran out of budget. 1337 unit tests, 36 Docker tests. Nothing has run with a real model yet. Entry in `LEARNINGS.md`.
- **Two harder tasks** `t015_path_traversal` (CWE-22: sibling folder with the same start, links out of the base, NUL byte) and `t016_ssrf_guard` (CWE-918: private, metadata, IPv6 and mapped addresses, names that point inside, numbers written in other ways, with DNS handed in). Both pass the cross-check. A half fix of t015 leaves a visible test red; the usual wrong fixes of t016 pass every visible test and fail hidden ones. Entry in `LEARNINGS.md`.
- **Act II review** against the anti-pattern checklist (greps + reading): clean except one silent cut in the `whitespace_mismatch` error (fixed, test first, shared `_listed`). 908 unit tests. Entry in `LEARNINGS.md`.
- **Act I review** against `plans/shared/anti-patterns.md` (14 items, checked with grep): clean, except two docstrings
  that promised more than the code did (`build_app`, `read_file`; fixed), the `LLMError` wrapping decision that was
  not implemented yet (now marked for M4), and two small notes (silent cut in a CSV column, `connect_ex` not blocked).

## In Progress

- (nothing; M5 is done and waits for the commit and the LEARNINGS rewrite)

## Backlog

### P0 (next up)

- M13 still to do: a cheap probe (one run, 1 seed, about $0.20) on t015 and t016 to see whether the first attempt misses; tasks where the first attempt is likely to miss (t009/t011/t014 were all fixed at the first attempt, by the loop and by one run: `bench/results/2026-11-25T16-37_af6aa0.jsonl`, `2026-11-25T16-38_228aff.jsonl`), then a measurement with 3 seeds or more; read the state file to continue a run; the `Goal` that a model writes from an issue (grill -> goal); the measurement (t009, t011 and t014 now have a test the agent can see; the three of M10 have none, so the loop would end there as `unverifiable`).

- M12 step 5 (scheduled CI gate): needs a CI first, and a baseline with SEM, so it waits for M11. See the Decisions Log.
- Look at t001: is its issue enough to know that negated phrases must stay unflagged? (the first real run failed there)
- The hidden tests of t014 do not say whether shell characters inside quotes (`grep "a;b" f`) may run; the reference fix refuses them. Decide before the task is used to compare agents.
- Decide about `bench/results/`: commit the `.jsonl` results, ignore `bench/results/trajectories/`.
- Close Act I: tick the milestone boxes and the end-of-Act checklist in `plans/act-1-api.md`.
- Close M5: rewrite the LEARNINGS entry in your own words, tick the boxes in `plans/act-2-harness.md`, commit.
- When the prompt, a tool description or a hook changes: record the cassette again (`bench.run --record` with `--task` for the three recorded tasks, about $0.20), or the Docker replay test fails with a `CassetteMiss`.
- Check `bench/pricing.yaml` against the official price page (it was copied from a cached table of 2026-11-03).
- Use `ruff check . --no-cache` before a commit (the cache hid 4 errors, now fixed).
- Check `test_command` of the report (it is trusted today), and decide whether the hook mechanisms should try the hook instead of looking for it.
- Decide whether the tests that start real MCP servers (about 20 s of the default run) get the `slow` marker.
- Sandbox for MCP servers written by others (needs interactive stdin in `DockerSandbox`); before M21.
- See a real model get sent back by the stop hook (needs a task where a fix is not obviously complete).
- Open from M3: what to do after a `TRUNCATED` or refused reply (retry or give up) belongs to the caller, so decide it
  when the first real caller (the CLI or a worker) exists.

### P1

- Act II: ReAct loop, workspace, edit tools, sandbox, hooks, MCP client, `AGENTS.md` contract, versioned prompts (M4-M9b).

### P2

- **M11 Benchmark v1** (deferred 2026-11-24): 20+ tasks, dev/heldout split, mean ± SEM with a paired comparison, edge-case and contract tests, `bench/PROTECTED`, quality gate. Waits on a real need for a bigger benchmark. Milestones that list it as a dependency: M12b, M13, S7, M21, M23, M27 (decide per milestone whether M10's three tasks are enough or a smaller step of M11 is pulled in first). Done so far: `prova/stats.py` has `mean_ci95`, `t_critical_95`, `percentile`.

- Act III onwards: benchmark, self-correction, security agent (Act V), memory, hardening, operations, self-improvement.

## Decisions Log

| Date | Decision | Why |
|---|---|---|
| 2026-11-21 | No framework for the core; plain Anthropic SDK | The goal is to understand every layer. LlamaIndex only as a comparison baseline (Act V, S2). |
| 2026-11-21 | Own benchmark built from real bugs, SWE-bench style | `openai/mle-bench` is too costly and its leaderboard is closed; only its design ideas are reused. |
| 2026-11-21 | Positioned as a security coding agent; defensive scope only | Differentiator is evidence: every finding ships with a runnable PoC. |
| 2026-11-22 | `uv` for the environment; `src/` layout | Pinned, hashed lockfile; tests import the installed package, not a stray local file. |
| 2026-11-22 | Code, comments and error messages in English | The repo is a portfolio piece; explanations in chat stay Vietnamese. |
| 2026-11-22 | M0a cannot test "registry frozen after build_app" | `App` had no registry until M4. Resolved in M4: `ToolRegistry` refuses a registry that is not frozen, so the rule holds by construction. |
| 2026-11-22 | Implemented in M4 (`AnthropicClient`): wrap every `anthropic.APIError` into `LLMError` (with `from e`) in `llm/`; no subclasses yet; no own retry | The SDK already retries connection errors, 408/409/429/5xx (2 retries); a second retry layer would multiply attempts. Split subclasses only when a caller needs different handling (M23). |
| 2026-11-22 | `UsageRecord` is a frozen dataclass; experiment CSVs carry no `schema_version` | Frozen follows the conventions for internal trusted values. The CSV is a throwaway experiment log; the trajectory log (M5) is a durable format and will carry `schema_version`. |
| 2026-11-22 | `LLMClient` stays minimal (`create` only); experiments call the SDK directly | The Protocol grows when Prova's own code needs it (`stream`, `system`/`tools` when the agent loop moves into `src/` at M4; `count_tokens` at M23). |
| 2026-11-22 | `resolve_inside` confines paths with `resolve()` + `is_relative_to`; the loop answers all tool calls of a turn in ONE user message | Resolving first defeats `..`, symlinks and sibling-prefix tricks. The API accepted split results in M2, but one message is the documented shape and keeps the pairing rule easy to reason about. Open: OS permission errors (TODO M4) and the check-then-read gap (sandbox, M7). |
| 2026-11-22 | `extract()` calls `messages.create(output_config=...)` and validates itself, not `messages.parse()` | `parse()` validates inside the call, so a truncated or refused reply raises a validation error and `stop_reason` is never visible. Structured outputs (method C) is the default; forced `tool_choice` is not used because the main model rejects it (HTTP 400). |
| 2026-11-22 | `StructuredError` carries a `StructuredErrorKind`; messages name the field and rule but never echo the model's output | Callers branch on the enum, not on text. Untrusted model output must not leak into logs or error messages. |
| 2026-11-22 | "3 seeds" means 3 repetitions; `ruff` skips `experiments/act1/m3_cases/` | The API has no `seed` and sampling parameters were removed. The snippets are deliberately buggy sample data, never imported. |
| 2026-11-22 | Permissions are enforced in `ToolRegistry.dispatch` (from `RunContext.permissions`), not only in the tool list shown to the model | The model can ask for a tool it was never offered (a hallucination or an injected instruction). The check runs before the arguments are validated, so a denied tool reveals nothing about what it expects. |
| 2026-11-22 | `search_code` is pure Python and matches literal text, not regular expressions | `ripgrep` is not installed here, and every extra binary in the sandbox image is one more thing to pin (M7). A model-supplied regex can hang `re`, which has no timeout. Regex comes back together with a real timeout. |
| 2026-11-22 | Any `stop_reason` other than `end_turn` and `tool_use` ends the run, and no tool in such a reply is run; no retry of our own | A cut or unrecognised reply is not a trustworthy request (its arguments may be incomplete). The SDK already retries transient errors, so an `LLMError` means it has given up. |
| 2026-11-22 | The deny-list is checked inside `resolve_inside`, on the resolved path, case-insensitive; a protected path gets the same `permission_denied` whether or not it exists | One place means no tool can forget it, and resolving first means a symlink cannot smuggle a secret out. The same message for both cases leaves no way to learn which files exist. |
| 2026-11-22 | `list_dir` hides protected entries; `search_code` skips them and counts them as "protected" | A listing that shows a name the model may not open invites probing. A search needs to say it was partial, but not which file was skipped. |
| 2026-11-22 | Profiles (`permissions.yaml`) are default-deny and every profile also gets `DEFAULT_DENY_PATHS`; loaded with `safe_load`, any problem is a `ConfigError` | A profile can add protection but cannot remove the secrets baseline. A broken config must stop the run, not fall back to something permissive. |
| 2026-11-22 | Secrets are redacted twice: in the tool result before the model sees it, and in the trajectory before it reaches the disk; by shape and by the value of sensitive environment variables | The model must not see a key, and the log must not keep one. The environment is passed in explicitly so the library never reads `os.environ` on its own. |
| 2026-11-22 | The trajectory is JSONL with `schema_version`, `ts`, `run_id`, `seq`; opened and closed per event; a write failure raises `TrajectoryError` and stops the run; `tool_call` is logged before dispatch | An event is on disk when `write` returns, so a crash cannot lose it. A run that cannot be audited should not continue. |
| 2026-11-22 | Keep our own loop instead of the SDK `tool_runner` | In one run the SDK loop leaked a secret that was in an ordinary file (no redaction layer) and left no trajectory. The comparison had n=1 per side, so the token difference is not a finding. |
| 2026-11-24 | `edit_file` has three failure codes (`no_match`, `ambiguous`, `whitespace_mismatch`) and `create_file` adds `already_exists` | The model reads only the message, but the trajectory needs the reason as a field so the distribution of edit failures can be counted. |
| 2026-11-24 | `RunContext.write` defaults to matching nothing; every write tool checks `resolve_inside`, then `write_paths`, then touches the disk | Fail-closed: a run that was not given write paths cannot write. A path that is not writable must not reveal whether it exists. |
| 2026-11-24 | One in-memory `SnapshotStore` per run; the first `ensure` wins; `rollback_all` goes on after a failure; the caller decides when to roll back | The copy must be the original, not an earlier edit. Rolling back half is worse than reporting what failed. Whether a failed run keeps partial work is a policy of the caller (the script), not of the loop. |
| 2026-11-24 | Line numbers in `read_file` use `splitlines()`, like `search_code` | Two tools must number the same line the same way, or the model quotes a line the other tool cannot find. |
| 2026-11-24 | One container per command, `docker run --rm`, with `--network none`, `--read-only`, a 64 MB `noexec` `/tmp`, no root, all capabilities dropped; no fallback to the host | Nothing carries over between commands, and a command can only change the workspace. A missing Docker is an error, never a reason to run on the host. |
| 2026-11-24 | `run_tests` has a fixed command; the model gives only a `target`, passed after `--` | pytest options are a way to run other code or leave the workspace (`-p`, `--rootdir`). A fixed command needs no allowlist of arguments. |
| 2026-11-24 | A failing test run is a successful tool call that says "tests FAILED"; an unexpected exit code or a timeout is an error | Red tests are what the model needs to read, not a broken tool; the message and `passed: false` keep it from looking like a pass. |
| 2026-11-24 | The sandbox image is pinned by base digest and by package hash (`--require-hashes`); only python, pytest, ruff and coverage for now | A tag can be moved and a package can be replaced, so both are pinned. bandit and semgrep are heavy and wait for Act V. |
| 2026-11-24 | A hook can only tighten: `allow` never lifts a permission denial, which stays in `ToolRegistry.dispatch` | A hook written wrong must not open a hole. Permissions and paths are fences; hooks are extra rules. |
| 2026-11-24 | A hook that fails counts as no before a tool and at stop; after a tool it leaves the note "this check did not run" | A rule that could not be checked is not a rule that passed. After a tool there is nothing left to block, but the model must not read a missing lint as a clean one. |
| 2026-11-24 | After too many refusals the stop hook ends the run as `unverified`, not `done` | `done` must keep meaning "the model finished and the checks agreed", so scripts and the benchmark cannot count a refused run as a success. |
| 2026-11-24 | Lint and the test gate run in the sandbox; the path given to the container goes through `resolve_inside` | Code the model wrote never runs on the host, and a protected or outside path is never handed on. |
| 2026-11-24 | Each `McpConnection` owns a thread with an event loop; its methods are synchronous and wait with a time limit | The MCP SDK is asyncio and the rest of Prova is not. Moving the whole loop to async would touch every tool, the registry, the hooks and about 600 tests for one milestone. |
| 2026-11-24 | MCP servers run on the host as child processes with the SDK's minimal environment, and only those listed in `mcp.yaml` | Enough for the one server we wrote. It breaks "commands never run on the host", so a third-party server needs a sandbox first. |
| 2026-11-24 | A tool of an MCP server whose name, description or schema fails the checks is left out and reported, never repaired; the log names the rule, not the server's text | The description is the server's text and the model reads it, so cutting it would be editing it. The text is untrusted, so it is not copied into logs. |
| 2026-11-24 | The model gets the server's schema as it is and the arguments are not checked on our side; the answer shows text only and says what was left out | The server checks its own arguments. A schema validator would add a dependency and a second opinion about a schema we do not own. |
| 2026-11-24 | A configured server that cannot start stops the whole run, and the ones already started are closed | The config says it must be there. Going on without its tools would look like a run that had them. |
| 2026-11-24 | The agent's contract lives in `contract/` (`contract.yaml` for code, `AGENTS.md` for the model); the root `AGENTS.md` stays a guide for people | One file cannot serve two readers: the model of Prova would read `uv run` commands and lint rules as its own rules. |
| 2026-11-24 | Every rule names a mechanism, and at start-up the mechanism proves itself with a probe on the run's real configuration; a rule nothing enforces is written `prompt_only` and listed apart | A rule is only worth its place if something enforces it. Reading a config and reasoning about it is weaker than trying the real tool. |
| 2026-11-24 | Profiles have `write_deny_paths` (readable, never writable); it wins over `write_paths` and is checked on the resolved path | "Do not edit tests" could not be said with `deny_paths`, which forbids reading too, and the agent has to read the tests. |
| 2026-11-24 | The final report is the `submit_result` tool with the field names of `output_contract`; a stop hook compares it with `SnapshotStore.changed()` in both directions | Prose cannot be checked. What changed is read from the files, not from what the agent says it changed. |
| 2026-11-24 | A lint rule in the contract means "ruff runs after every edit", not "ruff is clean" | The lint hook only adds a note, so a rule that said more would be a rule nothing enforces. |
| 2026-11-24 | The system prompt is built from static layers and only names the tools the run is offered; the task goes in the user turn inside `<untrusted_issue>` | A read-only run must not hear the name of a tool that writes, and text from somebody else must never sit where the model reads facts. |
| 2026-11-24 | `prompt_hash` is sha256 of the system prompt and the tool definitions, and the `task` event always has the key (`null` = not said) | Two runs of the same task can be told apart by one number (M16); an absent key and a null would mean different things to a reader. |
| 2026-11-24 | M11 (Benchmark v1) goes to the backlog; M10's three tasks stay the benchmark for now | A benchmark "big enough to trust" is premature before there is something to compare. Pull in only the files a later milestone needs. Cost: milestones that depend on M11 must be checked one by one. |
| 2026-11-24 | M12 closed without its scheduled CI gate (step 5) | The repo has no CI, and a gate that compares with a baseline needs the bigger benchmark of M11, which is deferred. The local form exists: the Docker replay test fails when a change of the harness moves the chain of tool calls. |
| 2026-11-24 | The M13 loop is outside the stop hook, keeps the patch between attempts, and is `stuck` after 2 equal fingerprints in a row | The hook already sends the agent back twice with raw output; the loop adds a fresh context, a record and limits. Keeping the patch lets a partial step count. Both numbers are guesses to be measured. |
