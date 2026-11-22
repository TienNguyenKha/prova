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
- **M2** First hand-written tool: `prova/llm/tool_messages.py`, `prova/tools/fs.py` (confined `read_file`),
  25 unit tests, `m2_tool_loop.py`, `m2_break_rules.py`, `m2_stream_tool.py`. Rules table and answers in `LEARNINGS.md`.
- **M3** Structured output: `prova/structured.py` (`extract`, `check_stop_reason`, `parse_structured`,
  `BugClassification`), `StructuredError` + `StructuredErrorKind`, `prova/stats.py` (`mean_sem`), 30 unit tests,
  20 snippets in `experiments/act1/m3_cases/`, and four hand-run scripts. Both comparison tables are in `LEARNINGS.md`.
- **Act I review** against `plans/shared/anti-patterns.md` (14 items, checked with grep): clean, except two docstrings
  that promised more than the code did (`build_app`, `read_file`; fixed), the `LLMError` wrapping decision that was
  not implemented yet (now marked for M4), and two small notes (silent cut in a CSV column, `connect_ex` not blocked).

## In Progress

- (nothing yet; next is M1)

## Backlog

### P0 (next up)

- Close Act I: tick the milestone boxes and the end-of-Act checklist in `plans/act-1-api.md`.
- M4: read-only ReAct loop (Act II). Open from M3: what to do after a `TRUNCATED` or refused reply (retry or give up)
  belongs to the caller, so decide it when the first caller exists.

### P1

- Act II: ReAct loop, workspace, edit tools, sandbox, hooks, MCP client, `AGENTS.md` contract, versioned prompts (M4-M9b).

### P2

- Act III onwards: benchmark, self-correction, security agent (Act V), memory, hardening, operations, self-improvement.

## Decisions Log

| Date | Decision | Why |
|---|---|---|
| 2026-11-21 | No framework for the core; plain Anthropic SDK | The goal is to understand every layer. LlamaIndex only as a comparison baseline (Act V, S2). |
| 2026-11-21 | Own benchmark built from real bugs, SWE-bench style | `openai/mle-bench` is too costly and its leaderboard is closed; only its design ideas are reused. |
| 2026-11-21 | Positioned as a security coding agent; defensive scope only | Differentiator is evidence: every finding ships with a runnable PoC. |
| 2026-11-22 | `uv` for the environment; `src/` layout | Pinned, hashed lockfile; tests import the installed package, not a stray local file. |
| 2026-11-22 | Code, comments and error messages in English | The repo is a portfolio piece; explanations in chat stay Vietnamese. |
| 2026-11-22 | M0a cannot test "registry frozen after build_app" | `App` has no registry until M4; test is a `TODO(M4)` in `tests/unit/test_app.py`. |
| 2026-11-22 | Decided, to implement with the first caller (M4): wrap every `anthropic.APIError` into `LLMError` (with `from e`) in `llm/`; no subclasses yet; no own retry | The SDK already retries connection errors, 408/409/429/5xx (2 retries); a second retry layer would multiply attempts. Split subclasses only when a caller needs different handling (M23). |
| 2026-11-22 | `UsageRecord` is a frozen dataclass; experiment CSVs carry no `schema_version` | Frozen follows the conventions for internal trusted values. The CSV is a throwaway experiment log; the trajectory log (M5) is a durable format and will carry `schema_version`. |
| 2026-11-22 | `LLMClient` stays minimal (`create` only); experiments call the SDK directly | The Protocol grows when Prova's own code needs it (`stream`, `system`/`tools` when the agent loop moves into `src/` at M4; `count_tokens` at M23). |
| 2026-11-22 | `read_file` confines paths with `resolve()` + `is_relative_to`; `build_tool_results` always sends ONE user message | Resolving first defeats `..`, symlinks and sibling-prefix tricks. The API accepted split results in M2, but one message is the documented shape and keeps the pairing rule easy to reason about. Open: OS permission errors (TODO M4) and the check-then-read gap (sandbox, M7). |
| 2026-11-22 | `extract()` calls `messages.create(output_config=...)` and validates itself, not `messages.parse()` | `parse()` validates inside the call, so a truncated or refused reply raises a validation error and `stop_reason` is never visible. Structured outputs (method C) is the default; forced `tool_choice` is not used because the main model rejects it (HTTP 400). |
| 2026-11-22 | `StructuredError` carries a `StructuredErrorKind`; messages name the field and rule but never echo the model's output | Callers branch on the enum, not on text. Untrusted model output must not leak into logs or error messages. |
| 2026-11-22 | "3 seeds" means 3 repetitions; `ruff` skips `experiments/act1/m3_cases/` | The API has no `seed` and sampling parameters were removed. The snippets are deliberately buggy sample data, never imported. |
