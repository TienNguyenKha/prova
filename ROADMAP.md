# ROADMAP

Source of truth for *what is happening now*. The long-term plan lives in `PLAN.md` and `plans/`.
Update this file at the end of every working session.

## Implemented

- **M0a** Repo foundation: `uv` environment and tool config, `errors.py`, `config.py`, `registry.py`,
  `llm/client.py`, `app.py`, `tests/` (network block, fakes, 40 unit tests), `AGENTS.md`, `grill-me` command.
  `ruff`, `pyright` (strict) and `pytest` are green.
- **M0** One real API call: `experiments/act1/m0_first_call.py`, `m0_variants.py`, `m0_count_tokens.py`,
  `m0_errors.py`. Error table and `stop_reason` notes are in `LEARNINGS.md`.

## In Progress

- (nothing yet; next is M1)

## Backlog

### P0 (next up)

- M1: multi-turn conversation and streaming.
- M2: first hand-written tool.
- M3: structured output.

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
| 2026-11-22 | Wrap every `anthropic.APIError` into `LLMError` (with `from e`) in `llm/`; no subclasses yet; no own retry | The SDK already retries connection errors, 408/409/429/5xx (2 retries); a second retry layer would multiply attempts. Split subclasses only when a caller needs different handling (M23). |
