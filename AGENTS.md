# AGENTS.md

Instructions for any coding agent (or human) working in this repository.

## What this is

**Prova** is a security coding agent built from scratch on the plain Anthropic SDK (no agent framework in the core).
Principle: **"No proof, no finding."** Every reported vulnerability must come with a runnable PoC test that executes
in a sandbox. Scope is **defensive only**: the user's own code or code they are authorised to test.

Start every session by reading `ROADMAP.md` (what is in progress, next P0) and `LEARNINGS.md`.
The full plan is in `PLAN.md` and `plans/`.

## Commands

```bash
uv sync                      # install from uv.lock
uv run ruff check .          # lint (includes Bandit rules, bare-except and print bans)
uv run pyright               # types (strict for src/)
uv run pytest                # unit tests; skips `live` and `docker` by default
```

All three checks must be green before every commit. Run things through `uv run`; do not activate a venv.

## Conventions (details: `plans/shared/code-conventions.md`)

- Python 3.12+, `src/` layout: the package is `src/prova/`.
- **Only `load_settings()` reads the environment**, once, at program start. Every other layer receives `Settings`
  as a parameter. `import prova` must never need a `.env` or a key.
- Create things in **one** place, `build_app` (`src/prova/app.py`). Pass dependencies in as parameters
  (`llm`, `settings`, clock, sandbox, ...). Never import a client or settings from another module.
- Registries: explicit registration in `build_app`, duplicate names raise, `freeze()` afterwards. No
  import-time decorator registration.
- Errors: failures caused by the **model** (bad tool call, bad args) are data (`ToolResult`); failures caused by
  **our code** are exceptions (`ProvaError` tree in `errors.py`). No bare `except Exception` outside the listed boundaries.
- Interfaces are `Protocol`, not ABC. Data crossing a boundary is a Pydantic model with `extra="forbid"`; internal
  trusted values are frozen dataclasses. Do not pass bare `dict` through layers.
- Files written to disk carry a `schema_version`.
- Code, comments, docstrings and error messages are in **English**.
- `print` only in `experiments/` and the CLI.

## Testing

- Write the failing test first, see it fail, then write the code. Fix a bug the same way (test, then fix, one commit).
- Unit tests never touch the network: `tests/conftest.py` blocks sockets unless a test is marked `live`.
- Test fakes live in `tests/fakes.py` (`FakeLLM`, ...). Prefer passing a fake in over `patch`/`monkeypatch`;
  `monkeypatch` is only for things that cannot be injected (stdlib, third-party internals).
- Error paths get as many tests as success paths.

## Do not

- Do not commit `.env` or print/log API keys (`Settings.anthropic_api_key` is a `SecretStr`).
- Do not add a dependency before a milestone needs it (`uv add` at the point of use; commit `pyproject.toml` + `uv.lock`).
- Do not hard-code model names outside the defaults in `Settings`.
- Do not run security tooling against systems you are not authorised to test. PoCs target functions in this repo,
  in a sandbox without network.
- Do not fix a bug without recording a regression test for it.
