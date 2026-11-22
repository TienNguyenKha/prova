# LEARNINGS

One entry per milestone: what was built, what broke, what was learned.
The questions to answer are listed under "Questions for LEARNINGS.md" in each milestone of `plans/`.

<!-- Entry template:

## M<N>: <title> (<date>)

- Built:
- Broke / surprised me:
- Learned:
- Answers to the milestone questions:

-->

## M0a: Repo foundation (2026-11-22)

- **Built:** `uv` environment (src layout), error tree, frozen `Settings` + `load_settings`, `Registry[T]`,
  `LLMClient` Protocol + `make_client`, `build_app`, test infrastructure (network block, `FakeLLM`), 40 unit tests.
  Order was test first for config, registry and app; each test was seen failing before the code existed.
- **Broke / surprised me:**
  - `uv init` filled `authors` from `git config`, which was my work email. Easy to leak into a public repo.
  - My shell had `VIRTUAL_ENV` pointing at the `atlas-agents` venv. `uv` ignored it with a warning, but it shows how
    easily two projects share an environment by accident.
  - The plan's verification table asks for "registry frozen after `build_app`", but `App` has no registry until M4.
    The plan and the step list disagreed; I logged it in the Decisions Log instead of faking a test.
  - English text is longer than Vietnamese: several comments and messages broke the 110-column ruff limit.
  - I expected pyright (strict) to complain that the SDK's overloaded `messages.create` does not match my small
    `MessagesAPI` Protocol. It did not.
- **Learned:** the config rules only help if something runs them; nothing is automatic until CI (M10), so the three
  commands (`ruff`, `pyright`, `pytest`) are a manual gate before every commit. Passing `environ` and `env_file` as
  parameters made every config test trivial, with no `patch` and no `os.environ` edits.

### Answers to the milestone questions

**Why is a `Protocol` easier to test with than an ABC? How many lines is a `FakeLLM`?**
A `Protocol` is checked by shape, so a fake only needs the methods the code under test calls. An ABC forces the fake to
inherit from the base class (and import it, and satisfy every abstract method), which couples tests to the production
hierarchy. My `FakeLLM` is 5 lines (`tests/fakes.py:46-50`); with the recording `FakeMessages`, the `Call` record and the
`text_message` helper the whole file is about 50 lines. It satisfies `LLMClient` without inheriting from anything, and
it also records every call, so tests can assert on what was sent to the API.

**Which errors should be exceptions and which should be data (`ToolResult`)? Where is the boundary?**
The question is *who caused the failure*. If the **model** caused it (called a tool that does not exist, sent bad
arguments), it is data: return an error result so the model can read it and correct itself. If **our code** caused it
(duplicate registration, `get` with a name we never registered, missing config), it is a bug or a setup mistake and
must be an exception, loud and early. This is why `Registry` has both `get` (raises `UnknownNameError`) and `find`
(returns `None`, for names that come from the model). The conversion from exception to data happens at exactly one
place, the tool boundary (M4); the old `"Error: ..."` strings in `atlas-agents` mixed the two and hid failures.

**Why must the tool order be stable?**
Two reasons. (1) **Prompt caching** (M23): the tool list is part of the request prefix, and a cache hit needs a
byte-identical prefix; if the order changes between turns or runs, the cache misses and every turn costs full price.
(2) **Reproducibility**: the model sees a different prompt if tool order changes, so benchmark results would differ
between runs for reasons unrelated to the agent. `Registry.__iter__` therefore sorts by name instead of using
registration order, so the result does not depend on import order or on who registered first.

## M0: One real API call (2026-11-22)

- **Built:** four hand-run scripts in `experiments/act1/` (first call, parameter variants, `count_tokens`, provoked
  errors). They call the SDK directly, not through `LLMClient`: the Protocol only grows when Prova's own code needs it.
- **Broke / surprised me:**
  - `input_tokens` is larger than the text I typed: "Count from 1 to 10." is about 8 tokens of text but 18 input
    tokens. Each message carries fixed framing, and a `system` of about 6 tokens added 10. Because the API is
    stateless, this overhead is paid again on every turn.
  - `output_tokens` is also larger than the visible text (30 for `'1, 2, ..., 10'`, 45 for a two-line list) and
    `thinking_tokens` was 0. I do not know why. Open question.
  - `max_tokens=-1` is not rejected by the SDK: the request goes out and the **server** answers 400.
- **Learned:** the SDK retries only what is plausibly transient and never retries a 4xx other than 408, 409 and 429.
  Checked in `_base_client.py` (`_should_retry`, `DEFAULT_MAX_RETRIES = 2`).

### Error table

| Situation | Exception | HTTP | SDK retries? | Evidence |
|---|---|---|---|---|
| Wrong model name | `NotFoundError` | 404 | no | one `HTTP Request` log line |
| Invalid API key | `AuthenticationError` | 401 | no | one log line |
| `max_tokens=-1` | `BadRequestError` | 400 | no | server-side validation, one log line |
| Empty message content | `BadRequestError` | 400 | no | one log line |
| Connection refused | `APIConnectionError` | none | **yes, 2 retries** | two "Retrying" lines (0.48s, 0.91s), 1.40s total |

All derive from `APIStatusError > APIError > AnthropicError` (connection errors skip `APIStatusError`).
Retry on 408/409/429/5xx is read from the SDK source, not observed (the server cannot be made to return them on demand).
Decision (see ROADMAP): wrap every `anthropic.APIError` into `LLMError` with `from e`; no subclasses, no own retry.

### Answers to the milestone questions

**Which values can `stop_reason` take, what do they mean, and how should code react?**
Observed in this milestone: `end_turn` (the model finished by itself: the answer is complete), `max_tokens`
(cut off at my limit: the text is **incomplete**, so never treat it as a finished answer; raise the limit or continue),
`stop_sequence` (a configured string was produced; the matched string is in `stop_sequence` and is **not** part of
the text). Not observed yet, listed from the API docs and to be verified when used: `tool_use` (the model wants a
tool run: execute it and send back a `tool_result`; this drives the loop in M2/M4), and further values such as
`pause_turn` and `refusal`. The rule for code: branch on `stop_reason` explicitly and make unknown values an error,
never assume `end_turn`.

**Do `count_tokens` and `usage.input_tokens` match? Why?**
Yes, exactly, on all 3 inputs tried (19/19, 18/18, 28/28, with and without `system`). Both count the whole request as
the model sees it, including the framing around messages and `system`, not only my characters. Only 3 inputs were
tried (no tools, images or long prompts), so this is "matched on what I tried", not "always matches". It is useful
for budgeting before paying for a generation (M17, M24).
