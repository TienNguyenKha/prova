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
  - My shell had `VIRTUAL_ENV` pointing at another project's venv. `uv` ignored it with a warning, but it shows how
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
place, the tool boundary (M4); returning failures as plain `"Error: ..."` strings mixes the two and hides them.

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

## M1: Multi-turn chat and streaming (2026-11-22)

- **Built:** `prova/llm/usage.py` (`UsageRecord`, `UsageLog`, CSV export; unit-tested, test first), and four
  hand-run scripts: `m1_chat.py` (interactive and `--auto`), `m1_plot.py`, `m1_stream.py`.
  Step 6 (stream with a tool, `input_json_delta`) is deferred to M2: there is no tool yet.
- **Broke / surprised me:**
  - Each turn's `input` is about the previous `input` + previous `output` + ~20 tokens (new message and framing).
    The one exception was the turn after an answer cut by `max_tokens` (about 40 tokens lower than predicted).
    Unexplained; probably related to the open `output_tokens` question from M0.
  - "About 100 words" in Vietnamese produced ~350 output tokens; "about 150 words" produced ~500.
    I predicted 150-300. Vietnamese costs more tokens per word than I assumed.
  - One `content_block_delta` is not one token: ~5-6 tokens per delta, and deltas arrive in bursts.
  - The IDE flagged `csv_path` as possibly unbound (assigned inside `try`, used in `finally`); `pyright` does not
    check `experiments/`, so only the IDE caught it.
- **Learned:** the cost of a chat is the history, not the new message. Latency, on the other hand, did not grow with
  input in this range (about 2.0-2.4s for 42 to 5490 input tokens on Haiku): it follows the output length.

### Token usage over 15 scripted turns (Haiku)

![Input and output tokens per turn, and cumulative input tokens](experiments/act1/results/m1_tokens.png)

| | Turn 1 | Turn 15 | Total |
|---|---|---|---|
| input per turn | 42 | 5490 | 40,607 (cumulative) |
| output per turn | 282 | 338 | 5,224 (mean ~348) |

Input grows ~390 tokens per turn, so the cumulative input is quadratic: `42*N + 390*N*(N-1)/2` gives ~41,600 for
N=15 (measured 40,607). Extrapolating, 30 turns would cost about 170,000 input tokens, ~4x the cost of 15 turns.
Without history (only the new message each turn) the same 15 turns would cost about 15 * 42 = 630, so resending the
history multiplied the input by ~60. This extrapolation is from one run, not a measured 30-turn chat.

### Streaming: TTFT versus total time (3 runs per model)

| Model | TTFT (mean) | Total (mean) | TTFT / total | Tokens per second after first token |
|---|---|---|---|---|
| claude-haiku-5-5 | 0.80s | 2.64s | 30% | ~260 (derived) |
| claude-sonnet-5-5 | 1.13s | 3.86s | 29% | ~190 (derived) |

`message_start`, `content_block_start` and the first delta arrive within ~2 ms of each other: the wait is all before
the first event. Only the total time separates the models cleanly; TTFT/total ranged 22-36% across runs (n=3), so
30% vs 29% is noise.

### Answers to the milestone questions

**How much of the total time is TTFT? What does streaming help with, and what does it not?**
About 30% on both models (n=3, noisy). Streaming helps the person watching: they see the first text after ~0.8-1.1s
instead of waiting 2.6-3.9s, about 3x sooner, and a UI can show progress or let them cancel early. It does **not**
shorten the total time to a complete answer, reduce tokens or cost, or stop the input from growing with history. For
an agent it barely matters: code needs the whole message to know `stop_reason` and the tool arguments, so streaming
here is a display feature, not a speed feature.

**Why is turn 10 more expensive than turn 1?**
The API is stateless, so every turn resends the whole history. Input tokens grow linearly with the turn number
(~390 per turn here) and the total bill grows quadratically. This is the motivation for compaction (M17) and prompt
caching (M23).

## M2: First hand-written tool (2026-11-22)

- **Built:** `prova/llm/tool_messages.py` (`build_tool_results`: tool_use blocks in, one user message of
  tool_results out), `prova/tools/fs.py` (`read_file` confined to a root, `FS_TOOLS`, a tiny dispatcher),
  `ErrorCode.PERMISSION_DENIED`, 25 unit tests (test first), and three hand-run scripts: `m2_tool_loop.py`,
  `m2_break_rules.py`, `m2_stream_tool.py`, with sample files in `experiments/act1/sandbox/`.
  (In M4 the hand-written pieces were replaced by `agent/loop.py` and the tool classes; `m2_tool_loop.py` was
  deleted, the other two scripts now use the app's tools. Git history keeps the originals.)
- **Broke / surprised me:**
  - The model called **both tools in one turn** (parallel `tool_use`), in every run, not one after the other.
  - The first turn cost 421 input tokens for a one-line task (about 20-40 without tools). The tool definitions
    seem to cost ~380 tokens, and, like the history, they are billed on every call. Inferred from the size of
    the gap; I did not measure it in isolation.
  - The plan expected splitting tool_results across messages to be rejected. **The API accepted it**, and the
    model answered correctly (see the table).
  - Same input, different wording on each run (no `temperature` set), e.g. "No, they don't match" vs "No, they're
    different".
  - With `--max-iterations 1` the tools had run, but the conversation ended with no final answer: the cost was paid,
    the result was not collected.
- **Learned:** the API pairs every `tool_use` with exactly one `tool_result` by id, in two *adjacent* messages.
  Our own code is what keeps the pairing right: `build_tool_results` is a pure function with tests, so the rule is
  checked without calling the API.

### Rules broken on purpose (real API responses)

| # | What I broke | Result | What the API said (short) |
|---|---|---|---|
| 0 | nothing (control) | accepted | |
| 1 | wrong `tool_use_id` | 400 | `tool_result` has no matching `tool_use` in the previous message |
| 2 | `tool_use` removed from the assistant turn | 400 | same message as #1, with the real id |
| 3 | `tool_result` with nothing before it | 400 | same message, position `messages.0` |
| 4 | two `tool_result`s in two user messages | **accepted** | model answered correctly |
| 5 | one of two `tool_use` left unanswered | 400 | `tool_use` ids found without `tool_result` immediately after |
| 6a/6b | error text with / without `is_error` | accepted | replies were almost identical |

Three different violations (#1, #2, #3) give the same message: for the API they are all "this id has no `tool_use`
in the previous message". The position (`messages.2.content.0`) tells which block is wrong. #5 is the opposite
direction of the same rule.

Why does #4 pass? `m2_same_role.py` sent the same two sentences as one user message (A) and as two consecutive
user messages (B), without tools: both were accepted, answered `Linh`, and cost the same 28 input tokens. That
supports the guess that the API merges consecutive messages of the same role before processing them. One run on one
short pair, and I do not know how they are joined. It also means the M1 trap "two consecutive same-role messages make
the API fail" no longer holds for this API. Our code still sends one message and keeps roles alternating, so it does
not depend on this behaviour.

### Tool errors and `is_error`

A missing file comes back as `tool_result` with `is_error=True` and `not_found: no such file: missing.txt`. The model
said plainly that the file does not exist and did not invent content. With and without the flag, the replies were
almost identical (one sample each), because the text already says what happened. Not tested: ambiguous error text
(empty string, just `0`), where the flag may matter more.

### Streaming a request that uses a tool

A `tool_use` block starts with its `name` and `id` but an empty `input`; the arguments then arrive as pieces of a JSON
string (`input_json_delta`): `''`, `'{"path"'`, `': "note'`, `'s.txt"}'`. The pieces are cut mid-word and are not valid
JSON alone. The SDK's `snapshot` stays `{}` until the last piece. So a tool must only run after `content_block_stop`.

### Answers to the milestone questions

**Why does the API insist on keeping the `tool_use` block in the history?**
Observed: when I removed it (#2), the request was rejected, because validation matches each `tool_result` to a
`tool_use` by id in the previous message. My reasoning, not tested: the API is stateless, so the history we resend is
the only record of what the model asked for; a result only means something as the answer to that specific call.

**What does the model do differently with `is_error: true` than with a plain "Error: ..." string?**
In this experiment, nothing visible: both replies said the file does not exist and offered to try another path (one
sample each). I cannot say what the flag changes inside the model. I keep setting it for every `ToolError`, because it
is an explicit machine-readable signal and costs nothing, and I write the text so that it explains the failure by itself.

## M3: Structured output (2026-11-22)

- **Built:** `prova/structured.py` (`BugClassification`, `extract`, `check_stop_reason`, `parse_structured`),
  `StructuredError` carrying a `StructuredErrorKind`, `prova/stats.py` (`mean_sem`), 30 unit tests (test first),
  20 code snippets in `experiments/act1/m3_cases/`, and hand-run scripts: `m3_tool_choice_probe.py`,
  `m3_extract_smoke.py`, `m3_compare.py`, `m3_peek_failures.py`.
- **Broke / surprised me:**
  - I predicted that asking for "ONLY JSON" would break mostly through markdown fences. **No reply in 120 used a
    fence.** The real failures were prose, extra fields, and once a syntax error.
  - The same prompt works on one model and fails on the other: 98.3% valid on Haiku, 33.3% on Sonnet. A bigger
    model was much worse at prompt-only JSON.
  - Failures depend on the snippet: the same snippets failed in all 3 runs.
  - The SDK helper `messages.parse()` validates inside the call, so on a cut-off or refused reply you get a
    validation error and never see `stop_reason`. `extract()` therefore calls `messages.create(output_config=...)`
    and validates by itself.
  - There is no `seed` parameter and `temperature`/`top_p`/`top_k` were removed from the message methods, so "3
    seeds" are 3 plain repetitions.
  - One structured-output reply on Sonnet hit `max_tokens` at 4096 (the slowest call, 21.2 s). I do not know what it
    wrote; a classification normally needs about 150 tokens.
  - No reply was refused, although 9 of the 20 snippets are vulnerable code (SQL, SSRF, code execution).
  - `ruff` rule `S105` fired on a test variable named `secret`: the rule reacts to the name, not the value.
- **Learned:** a reply that is complete (`end_turn`) can still be unusable, and a reply that parses can still be
  the wrong shape. Both checks are needed, and which one fires says what went wrong.

### Forcing `tool_choice` (method B)

Same tool, only `tool_choice` changed:

| Model | `auto` (control) | `any` | `tool` (force `report_bug`) |
|---|---|---|---|
| claude-sonnet-5-5 | accepted | HTTP 400 | HTTP 400 |
| claude-haiku-5-5 | accepted | accepted | accepted |

The 400 message is `tool_choice: type "tool" and "any" are not supported for this model.` It gives no reason. So the
restriction is per model, not per model family. On Haiku, forced replies had only a `tool_use` block, while `auto`
also produced a `thinking` block (one observation, not tested as a rule).

### Comparison: 3 methods x 20 snippets x 3 runs (share of replies, %, mean ± SEM over 3 runs)

Haiku (claude-haiku-5-5), 60 replies per method:

| Method | ok | invalid_json |
|---|---|---|
| A. prompt "only JSON" | 98.3 ± 1.7 | 1.7 ± 1.7 |
| B. forced tool | 100.0 ± 0.0 | 0 |
| C. structured outputs (`extract`) | 100.0 ± 0.0 | 0 |

Sonnet (claude-sonnet-5-5), 60 replies per method (B is rejected on this model):

| Method | ok | extra_field | invalid_json | truncated |
|---|---|---|---|---|
| A. prompt "only JSON" | 33.3 ± 8.8 | 31.7 ± 6.0 | 35.0 ± 2.9 | 0 |
| C. structured outputs (`extract`) | 98.3 ± 1.7 | 0 | 0 | 1.7 ± 1.7 |

Reading it: on Haiku the methods cannot be told apart (the A failure is 1 reply of 60, about as large as its SEM).
On Sonnet the gap (33% vs 98%) is far larger than the SEM, so it is a real difference for this prompt. Latency
medians were the same for A and C (Haiku 2.8 s, Sonnet 3.1 s); forced tool on Haiku was faster (1.8 s mean vs 2.8 s),
cause not tested. Limits: one prompt for A (not tuned), 20 short snippets, formal validity only (not whether the
classification is correct).

What the failing replies looked like (method A, Sonnet):
- `invalid_json` (21): all `expected value at line 1 column 1`. The checked replies were complete answers
  (`end_turn`) written as prose ("The most important bug is that ...") instead of JSON.
- `extra_field` (19): valid JSON with a key that was not asked for, 15 times `severity_note`.
- On Haiku, the single failure was a syntax error at column 152 (cause unknown, raw text was not kept).

### Answers to the milestone questions

**In which ways does asking for JSON in the prompt fail, and which is the most dangerous for a guardrail?**
Observed: (1) prose instead of JSON, 35% on Sonnet; (2) valid JSON with extra keys, 32% on Sonnet; (3) broken JSON
syntax, 1 of 60 on Haiku. Not observed: markdown fences. The most dangerous kind is not a particular shape but any
failure that is routed to a "continue" path (fail-open). Prose is the worst case here: it is frequent, it looks like a
normal finished reply (`end_turn`), and it depends on the input, since the same snippets always triggered it. If an
attacker shapes the text being classified, they may be able to push the model into prose and walk through a
fail-open guard. That last step is an inference from the pattern, not something I tested. Fail-closed handling of
`StructuredError` removes the problem whatever the kind.

**Why did the API drop forced `tool_choice` while still keeping the output on schema?**
I do not know why the restriction exists: the error message gives no reason, and Haiku still accepts it. What I
measured: structured outputs produced no `invalid_json` and no `extra_field` in 120 replies on two models, which fits
the documented idea that generation is constrained to the schema. I did not verify that mechanism myself. It does not
guarantee completeness (one reply was truncated), and the SDK's `transform_schema` turns numeric limits such as
`ge`/`le` into plain text in the description (read from its source; not seen failing live, because no
`out_of_range` appeared). So the `stop_reason` check and the second validation stay.

## M4: A read-only ReAct loop (2026-11-22)

- **Built:** `tools/base.py` (`Tool`, `ToolResult`, `Permission`, `RunContext`), `tools/registry.py`
  (`ToolRegistry`: list tools for the API, dispatch the model's calls), three tools (`read_file`, `list_dir` in
  `tools/fs.py`, `search_code` in `tools/search.py`), `tools/paths.py` (`resolve_inside`), `llm/client.py`
  (`AnthropicClient`: SDK errors become `LLMError`), `llm/stop.py`, `validation.py`, and `agent/loop.py`
  (`run_agent`). 237 unit tests, all written before the code. `build_app` registers the tools and freezes the registry.
- **Broke / surprised me:**
  - Designing the loop exposed a hole: `dispatch` ran any registered tool, even one the model was never shown. A
    hallucinated or injected call to a write tool would have run. The permission check now lives in `dispatch`
    (before the arguments are even validated), and the tool list shown to the model comes from the same
    `RunContext.permissions`, so there is one source of truth.
  - A workspace is not a safe place just because paths cannot leave it: with the repo root as workspace,
    `read_file(".env")` is valid and would put the API key in the model's context and in the printed trace. The real
    run therefore works on a temporary copy of `src/`, `tests/` and `experiments/`. A deny-list for secret files and
    redaction in logs are P0 for M5.
  - `ripgrep` is not installed here: in the shell, `rg` is a function of the coding tool, which Python's
    `subprocess` cannot see. `search_code` is pure Python and matches literal text, not regular expressions: a
    model-supplied regex can hang the process (no timeout in `re`).
  - `Path.resolve()` raises `ValueError` on a NUL character, so a path the model sends can crash a tool. Found by
    thinking about inputs, not by a failing test.
  - The fake LLM stored a reference to the list the loop keeps appending to, so every recorded call looked like it
    had received the final history. It now copies the list at call time.
  - Two of my own mistakes, both caught by running the tests at once: `Tool` used `ToolResult` before it was
    defined (a `NameError`), and a "did you mean" test that passed even with no suggestion.
- **Learned:** every limit a tool applies (file size, entries, matches, line length, files scanned, skipped files)
  has to appear in the result. In the real run the model quoted "skipped 2 files" to warn that its own answer could
  be incomplete: that only worked because the tool said it.

### Real run: "Where is `check_stop_reason` defined, and where is it used?" (Sonnet, one run)

| Turn | Model call | Result sent back | Input tokens | Output tokens |
|---|---|---|---|---|
| 1 | `search_code("check_stop_reason")` | 14 matches in 4 files, 1,341 chars | 1,081 | 55 |
| 2 | `read_file("src/prova/structured.py")` | the whole file, 4,098 chars (111 lines) | 1,686 | 55 |
| 3 | final answer | | 3,390 | 583 |

3 calls, 6,157 input and 693 output tokens, status `done`. A plain text search (no model) finds the same 14 lines.
The answer accounted for all 14, with no invented line numbers. One slip in the reasoning: it said the call at
`tests/unit/test_structured.py:86` was probably "on a line the search didn't show", although the search had shown
it. It also named the 2 skipped files as a reason the list could be incomplete.

### Answers to the milestone question

**How long should a `snapshot` be: long enough for the model to understand, without bloating the context?**
I can only say what one run shows. The model needed a 1.3k-character search result and one 4k-character file, and
nothing was cut. Reading the 4k-character file added about 1,700 input tokens (about 2.4 characters per token for
code), and those tokens are paid again on every later turn (the quadratic cost from M1). The default of 20,000 bytes
per file would be roughly 8,000 tokens for a single read. That default is a guess, not a measured value: it stays a
constructor parameter, and a fair answer needs many questions, which is the job of the benchmark in Act III.
One thing the run suggests but does not prove: `read_file` returns no line numbers, so the model could only quote
line numbers that `search_code` had given it. Numbering the lines of a snapshot is worth trying, and M6 (editing)
will need it anyway.


## M5: Workspace, permission profiles, trajectory log (2026-11-22)

- **Built:** `harness/` (`patterns.py` path matcher, `permissions.py` profiles loaded from `permissions.yaml`,
  `redact.py`, `trajectory.py`), a deny-list inside `resolve_inside`, and the trajectory wired into `run_agent`.
  395 unit tests; one real run with a planted fake `.env`.
- **Decided:**
  - The deny-list lives in one place, `resolve_inside`, and is matched on the REAL resolved path, case-insensitive.
    A symlink named `notes.txt` that points at `.env` is still refused.
  - A protected path gets the same `permission_denied` message whether or not the file exists: no existence oracle.
  - `list_dir` hides protected entries silently; `search_code` skips them and counts them as "protected" in the
    skipped total, so the model knows its search was partial without learning which file it was.
  - Secrets are cleaned in two places: in the tool result before the model sees it, and again before a line reaches
    the trajectory file. Both use the same `ToolResult.model_text()`, so what is logged is what the model saw.
  - Redaction works by shape (`sk-ant-...`, private key blocks, ...) and by value (the values of environment
    variables whose name has the word KEY, TOKEN, SECRET, ...). The environment is passed in explicitly.
  - The log is part of the audit: if it cannot be written, the run stops (`TrajectoryError`). `task` is logged before
    the first model call and `tool_call` before the tool runs, so a hung tool still leaves a trace.
  - `model_request` is a summary (turn, model, message count, tool names), not the full text: the full text would be
    huge and full of secrets.
- **Real run** (`m4_ask_repo.py --bait`, `review` profile, Sonnet): 2 model calls, 2,702 input and 593 output tokens,
  status `done`. `read_file(".env")` returned `permission_denied` and the model reported that it did not try to get
  around it. `search_code("FAKE")` skipped `.env` and the model saw `[REDACTED]` where the fake key was in
  `src/notes.txt`. The trajectory has 10 events (`seq` 0 to 9) and `grep` finds neither `FAKE-KEY` nor `sk-ant` in it.
- **Learned:**
  - I expected `not_found` for the denied file and was wrong: `permission_denied` with the same text for an existing
    and a missing file is the right answer, because it is the same for both.
  - The model said that `.env` "may have been one of" the skipped files. A stated count is enough to make the
    model careful; it did not need the name.
  - The trajectory is checked with `jq`, not with eyes: `jq -c '{seq, type, tool, decision, error_code}'` gives the
    whole timeline in 10 lines.
- **Open:** redaction by shape and by value does not catch a secret that was transformed (base64, split in two
  lines). The real defence is that protected files never reach the model; redaction is the second layer.

### Against the plan's "done when" and its deviations

M5 has no question list in the plan, so I checked the milestone against its own checks.

- **Traversal tests:** `..`, an absolute path outside, a symlink pointing outside, a `~` (a plain name, never the
  home directory) and a NUL character are all covered in `tests/unit/tools/test_paths.py`. The check is
  `resolve()` + `is_relative_to`, never `startswith`.
- **Profile test:** a tool whose permission the run lacks is refused even when the model asks for it, and the
  permission is checked before the arguments (`test_tool_registry.py`). The plan names `edit_file`, which does not
  exist before M6, so the test uses a writer tool fake. When M6 adds `edit_file`, add the same test with it.
- **Redaction test:** a fake key in a tool result is written as `[REDACTED]`, and a test confirms the model gets the
  cleaned text too (`test_redact.py`, `test_loop_trajectory.py`).
- **"A run yields a trajectory readable with `jq`":** done, see the real run above.
- **No `request_permission` tool:** none exists, and the permissions of a run come only from the profile.

Where I went beyond or away from the plan:

- There is no `Workspace` class. A directory plus `resolve_inside` was enough, and the deny-list lives in the same
  function so that no tool can forget it. A class would be worth adding only if the workspace needs state (M7, the
  sandbox).
- The plan's deny-list example is `.env` and `**/*.pem`; the default is wider (keys, credentials, `.git`
  internals, ...) and every profile gets it on top of its own list.
- The plan lists ten event types; M5 has six (`task`, `model_request`, `model_response`, `tool_call`,
  `tool_result`, `outcome`). `hook_decision`, `verification`, `correction` and `escalation` should be added by the
  milestones that produce those events, not before there is something to log.
- Not done: `decision` is `allow` for an unknown tool name (the next event shows `not_found`), and a caller that
  forgets to pass a `redactor` gets shape-only redaction.


## M5b: The same question with the SDK's `tool_runner` (2026-11-22)

One run only, on the "bait" question (`m5_tool_runner.py --bait`). The tools are our own three, wrapped with
`beta_tool`, so only the loop differs. Our own loop ran the same question once (M5 real run).

| | Our `run_agent` | SDK `tool_runner` |
|---|---|---|
| Model calls | 2 | 2 |
| Input / output tokens | 2,702 / 593 | 2,333 / 676 |
| `.env` read | refused, `permission_denied` | refused, `permission_denied` |
| Fake key in the final answer | `[REDACTED]` | **shown in plain text** (line 44 of `m4_ask_repo.py`) |
| Trajectory log | 10 events, readable with `jq` | none (a traceback on stderr) |
| Code that drives the loop | `agent/loop.py`, 199 lines | about a dozen lines in the script |

- **What the SDK gave for free:** the loop itself, the pairing of `tool_use` and `tool_result`, a `max_iterations`
  limit, and the tool schema generated from a function signature and docstring. For a read-only question it
  worked the first time.
- **What our loop adds, and the run showed one of them:** the key leaked because nothing sits between the tool and
  the model that cleans the text. The deny-list kept `.env` out, but a secret that is already in an ordinary file
  (here the script's own source) went straight into the answer. Redaction in our loop is the layer that stops this.
  (I expected the leak to come from `notes.txt`, which this script does not create. It came from the script's own
  source, because the copied `experiments/` folder contains the fake key.)
- **Other differences, read from the code and the output, not measured:**
  - The SDK catches an exception from a tool and prints a traceback on stderr. The model quoted the exception text
    back to us, so that text reaches the model. Ours sends only the message of a `ToolError`; for a bug in a tool it
    sends a fixed sentence and keeps the traceback for the log.
  - Our wrapper skips `ToolRegistry.dispatch`, so a permission check at dispatch, the "did you mean" hint and the
    argument-error messages are not in this run. Wrapping our tools with `beta_tool` was a choice of this
    experiment, not a limit of the SDK.
  - No `stop_reason` table: the SDK's runner decides what to do with `max_tokens` or `refusal`; ours stops and says why.
- **The token numbers prove nothing.** One run each, a different tool description (generated, not hand-written),
  and a different answer length. Any claim that one is cheaper needs at least 3 runs per side (the rule from Act III).
- **Not done:** the first question (`check_stop_reason`) was not run with `tool_runner`.
- **Decision:** keep our loop. The reasons are the secret redaction, the trajectory, the permission check at
  dispatch and the stated `stop_reason` handling, which are the things this project is about. For a throwaway
  read-only script, `tool_runner` is a good choice.


## M6: Tools that change code, and a way back (2026-11-24)

- **Built:** `read_file` numbers its lines (`12│ code`); `edit_file` and `create_file` in `tools/edit.py`;
  `SnapshotStore` in `harness/snapshots.py`; `RunContext` gained `write` (where a run may write) and `snapshots`.
  466 unit tests, a self-contained fixture for task t001 (`experiments/act2/fixtures/t001/`) and a real-run script
  (`experiments/act2/m6_fix_t001.py`).
- **Decided:**
  - Line numbers use `splitlines()`, the same as `search_code`, so both tools give one line the same number even
    when the file has a form feed. A test guards it. The tool description says the numbers are not part of the file.
  - `edit_file` has three failure reasons with three error codes (`no_match`, `ambiguous`, `whitespace_mismatch`),
    and `create_file` adds `already_exists`. The model only reads the message; the codes are for the trajectory, so
    the reasons can be counted with `jq`.
  - The write path is checked in the same order in both tools: `resolve_inside` (outside, symlink, protected),
    then `write_paths`, and only then the disk. A path that is not writable does not reveal whether it exists.
    A run with no `write_paths` can write nothing.
  - `create_file` opens with `"xb"` (exclusive), so a file that appears between the check and the write is never
    overwritten. It also refuses to create a protected file such as `.env`.
  - The snapshot keeps the ORIGINAL (the first `ensure` wins), as bytes, in memory. Directories made for a new file
    are tracked and removed on rollback only if they are still empty. `rollback_all` goes on after a failure and
    reports all of them together. When the run should be rolled back is the caller's decision, not the loop's.
  - `abandon()` exists because a plain rollback after a failed `create_file` could delete a file somebody else made
    in the meantime: the snapshot would say "this file did not exist".
- **Real runs** (task t001, one-line regex fix; the agent cannot run commands yet, so the script runs `pytest` after
  it is done, in a minimal environment without the API key):

  | Model | Calls | Input / output tokens | Writing-tool calls | Tests after | Test file changed |
  |---|---|---|---|---|---|
  | sonnet-5-5 | 4 | 9,704 / 866 | 1 x `edit_file: ok` | pass | no |
  | haiku-5-5 | 4 | 10,454 / 1,337 | 1 x `edit_file: ok` | pass | no |
  | haiku-5-5 | 4 | 10,733 / 1,555 | 1 x `edit_file: ok` | pass | no |
  | haiku-5-5 | 4 | 10,945 / 1,675 | 1 x `edit_file: ok` | pass | no |

  All four runs took the same path: search, read both files, one edit, answer. The trajectory shows the model
  copied `old_str` without the `6│ ` prefix. The cheaper model used about 1.5 to 2 times the output tokens.
- **Edit failure rate: 0 of 4 calls.** This does not measure what the plan asks for. A one-line edit with no
  nested indentation and no repeated text is too easy: no run produced `no_match`, `ambiguous` or
  `whitespace_mismatch`, and the rollback path never ran in a real run (every run ended with "kept the change").
  Those branches are covered by unit tests only. A real distribution of reasons needs tasks that vary, which is
  the job of the benchmark in Act III.
- **Learned:**
  - I cut `fs.py` in the wrong place while moving a helper and deleted `ListDirTool` with it. The tests for the
    other file did not notice; a `grep` for the class did. After a refactor that moves code, check that every class
    is still there, not only that the tests pass.
  - A check that returned empty twice was wrong both times (a shell variable that did not split). A scan needs a
    positive control: something it must find.
  - The agent left a stale comment above the regex it fixed ("ignore all instructions"). The tests pass, but the
    change is not fully clean: a benchmark that only runs tests will not notice.
- **Open:** the snapshot is in memory, so a crash loses it; a failure of `rmdir` for a reason other than "not empty"
  is silent; and the test run is not a sandbox (it has the user's files and network). M7 replaces the last one.


## M7: A sandbox that can be swapped (2026-11-24)

- **Built:** `sandbox/base.py` (`ExecResult`, the `Sandbox` Protocol), `sandbox/docker.py` (`DockerSandbox`),
  `tools/run_tests.py` (`RunTestsTool`, permission `exec`), `docker/sandbox/` (a `Dockerfile` and pinned
  requirements), `RunContext.sandbox`. 526 unit tests that need no Docker, plus 18 that run real containers
  (`uv run pytest -m docker`).
- **The plan's table, run for real in containers:**

  | Case | Result |
  |---|---|
  | `while True: pass` | cut at the timeout, container removed |
  | small fork bomb | stopped by `--pids-limit` (the fork fails) |
  | connect to the internet | fails: no network |
  | write `/etc/passwd`, `/usr`, `/` | fails |
  | `["echo", "a", "&&", "touch", "/work/pwned"]` | `&&` is just an argument, no `pwned` file |
  | Docker not running | `SandboxUnavailableError`, nothing runs on the host |

  Also checked: the command is not root, the host's environment (including `ANTHROPIC_API_KEY`) does not reach
  the container, files outside the workspace are not visible, `/tmp` is writable but nothing in it can be
  executed, and no container is left behind after any test.
- **Decided:**
  - One container per command (`docker run --rm`), so nothing carries over between commands. `close()` only has
    to kill a command that is still running.
  - Every isolation flag has its own unit test on the command that is built, so a removed `--network none` is
    caught without Docker. I also checked that the network test can fail: with the flag deleted in a scratch
    script, the same code printed `CONNECTED`.
  - The arguments of the command come after the image name and are never joined into a string: a word that
    looks like a Docker option (`--privileged`) stays a plain argument. The workspace is passed with `--mount`
    (not `-v`), and a path with a comma is refused because it would end the option early.
  - `--read-only` with a small `/tmp` (64 MB, `noexec`): the plan asks that writing outside `/work` fails, and
    a non-root user alone would still be able to write to `/tmp`.
  - The model cannot choose pytest's options. `run_tests` has a fixed command and an optional `target` that goes
    after `--` and passes through `resolve_inside`, so `-p evil` is a missing file, not an option.
  - A failing test run is a successful tool call with "tests FAILED" in the message and `passed: false` in the
    data; only an unexpected exit code (for example 137) or a timeout is an error. This avoids a failure that
    looks like a success without treating "the tests are red" as a broken tool.
  - Without a sandbox in the context, or without Docker, `run_tests` refuses and says nothing was run. There is
    no fallback to the host, ever.
  - The image is pinned twice: the base by digest (not by tag) and the packages by hash (`pip install
    --require-hashes`), generated with `uv pip compile --generate-hashes`. Only python, pytest, ruff and coverage
    are in it; bandit and semgrep wait for Act V.
- **Real run** (t001, Sonnet, one run): search, read both files, `edit_file` (ok), `run_tests` (passed in 0.6 s),
  answer. 10,488 input and 790 output tokens. The script then ran the tests itself in the sandbox: 12 of 12
  passed, the test file was not changed, no container was left. The agent said "all 12 tests pass" and this time
  it had run them (in M6 it said it had not), and the claim was checked, not trusted.
- **Learned:**
  - My worry that user 1000 could not write to a macOS bind mount did not happen.
  - Removing a container is asynchronous after it stops, so a test that checks for leftovers right away would be
    flaky. `run` waits (up to 10 s) until the container is gone.
  - The first `docker run` can fail because the container does not exist yet when we try to kill it after a
    timeout. `_kill` retries a few times.
  - `uv` read a stale `VIRTUAL_ENV` that points into the old project's environment; `env -u VIRTUAL_ENV` fixed it
    for that one command.
- **Not tested, so not claimed:** the memory limit and the CPU limit are in the command (a unit test checks the
  flags) but no test shows a memory hog being killed; and slopsquatting protection (plan step 7) has nothing to
  protect yet, because there is no tool that installs packages and the network is off.
- **Open:** the local image tag `prova-sandbox:1` is not bit-for-bit reproducible (build timestamps); a run
  waits for each container to disappear, which costs one more `docker ps` per command; and the isolation is
  as strong as Docker's, with nothing of ours underneath.


## M8: Hooks, rules that live outside the loop (2026-11-24)

- **Built:** `harness/hooks.py` (`HookEvent`, `HookDecision`, `Hooks`, `run_hooks`), `harness/verifiers.py`
  (`LintAfterEdit`, `TestsBeforeStop`), `ToolResult.notes`, a `hook_decision` event in the trajectory, a new run
  status `unverified`, and `run_agent(hooks=...)`. 607 unit tests.
- **Decided:**
  - A hook can only make a run stricter. Before a tool it can say no; after a tool it can add a note; when the
    model wants to stop it can say "not yet". Permissions and path checks are not hooks: they stay in
    `ToolRegistry.dispatch`, and a hook's `allow` never lifts one (a test runs a "generous" hook next to a tool the
    run has no permission for, and the tool still does not run).
  - Where a no is possible, a hook that raises, or answers with something that is not a `HookDecision`, counts as a
    no. The model is told "the hook X failed", never the exception text; only the log has the text.
    After a tool there is nothing to block, so a failed hook leaves the note "this check did not run" instead,
    so a missing lint is not read as a clean lint. At stop, a hook that fails keeps the run going: nothing was
    verified.
  - The first `deny` ends the chain; after a tool every hook runs, so one failure does not hide the others.
  - A hook that blocks a tool gives the model the error code `safety` (a rule said no), not `permission_denied`
    (the run lacks the permission).
  - Several tool calls in one reply are still answered in one message in order, also when one was blocked by a hook.
  - The stop hook is only asked when the model ends normally (`end_turn`). After `max_stop_denials` refusals
    (default 2) the run ends as `unverified`, a status of its own, so no script or benchmark can count it as done.
  - Both verifiers run in the sandbox. `LintAfterEdit` compiles (builds the code, never runs it) and then calls
    `ruff check`; `TestsBeforeStop` runs pytest. A check that could not run (no sandbox, timeout, a killed process)
    keeps the model from stopping; a project with no tests at all (pytest exit code 5) may stop, with that said in
    the log.
  - A path that a hook is about to hand to the sandbox goes through `resolve_inside` first: `../x.py` and a file in
    the deny-list are skipped.
- **Real runs** (task t001, each checked by a separate pytest run in the sandbox, which passed every time):

  | Model | Condition | Calls | Stop hook |
  |---|---|---|---|
  | sonnet-5-5 | agent cannot use `run_tests` | 4 | allow |
  | sonnet-5-5 | normal | 4 | allow |
  | haiku-5-5 (x3) | agent cannot use `run_tests` | 4, 3, 4 | allow |
  | sonnet-5-5 | "change as little as possible" | 4 | allow |

  After each edit the model received `[lint_after_edit] injection_guard.py compiles and passes ruff`.
- **What was not seen live:** the stop hook never refused a real model. Each time the model's fix was already
  complete (it read the tests and covered them all, even when told to change as little as possible), so the gate
  said `allow`. The refusal path is covered by unit tests with a scripted model and by the hook against a real
  container (red tests give `continue`), but I have not watched a real model get sent back to work. The plan's
  second check ("the agent tries to finish with red tests and is returned") is therefore proven for the
  mechanism, not yet for a model.
- **Learned:**
  - `LintAfterEdit` is asked after every tool, so 4 of the 5 `hook_decision` lines of a run are empty `allow`s
    (after search and read). The log is complete but noisy; count lints with `jq 'select(.message != "")'`.
  - In the run where `run_tests` was hidden, the model still called it, because my task text named it. The
    permissions refused the call at dispatch: the tool list is not the fence, the check at call time is.
  - `run_tests` from M7 had no `-p no:cacheprovider`, so every run left a `.pytest_cache` in the agent's
    workspace. The hooks share the command with the tool now (`PYTEST_ARGV`), and `ruff` gets `--no-cache` for the
    same reason. A test of the real containers showed the workspace stays clean.
  - A class called `TestsBeforeStop` looks like a test class to pytest; it needs `__test__ = False`.
  - Unit tests with a fake sandbox could not have told me that the real `ruff` flags and the `compile` snippet
    work. Running both hooks once against real containers was the check that did.
- **Open:** there is no way yet to turn hooks on from a file (that is `contract.yaml`, M9); a stop hook that
  always says no costs `max_stop_denials` extra model calls before the run ends `unverified`; and ruff's
  "fixable with --fix" line reaches the model, which cannot act on it.


## M8b: Tools of other people's servers, behind the same fences (2026-11-24)

- **Built:** `prova/mcp/` with `config.py` (`mcp.yaml`), `tools.py` (checks, fingerprint, answer conversion),
  `connection.py` (`McpConnection`), `adapter.py` (`McpToolAdapter`, `discover_tools`) and `servers.py`
  (`open_mcp_servers`); `build_app(extra_tools=...)`; a repo-level `mcp.yaml`; two small servers in
  `tests/fixtures/mcp/` written for the tests; `experiments/act2/m8b_mcp_agent.py`. The package `mcp` (2.3.0) was
  added with `uv add`; it brings the whole server side of the SDK (starlette, uvicorn, ...), about 450 lines of
  `uv.lock`. 729 unit tests.
- **Probed before designing** (a scratch script, then the real SDK 2.3.0): a tool that raises is an answer with
  `is_error=True`, not an exception; a timeout is `MCPError` with code `REQUEST_TIMEOUT` and a dead server is
  `CONNECTION_CLOSED` (so the two are told apart by code, not by comparing text); every later call on a dead
  connection fails at once; a server that prints to stdout before serving only makes the client log a warning
  and go on; the server's stderr goes to our terminal unless it is redirected, and `Client(stdio_client(...,
  errlog=file))` is how to redirect it; a child process does not inherit our environment.
- **Decided:**
  - The SDK is asyncio and Prova is not. Each connection owns a thread running an event loop, and its methods
    hand work to that loop and wait with a time limit. No asyncio shows outside `prova/mcp/`.
  - The server runs on this machine as a child process, with the SDK's minimal environment. Only servers listed
    in `mcp.yaml` are ever started. This breaks the rule "commands never run on the host", and it is only
    acceptable because the one server used is ours. A server written by somebody else needs a sandbox first.
  - `mcp.yaml` has a mandatory permission per server and no defaults; a command is a list (never a shell
    line); an unknown key is an error, so a typo is not a silent default; the file is read with `safe_load`.
  - The model sees `mcp__<server>__<tool>`, and the description starts with "(a tool of the external MCP server
    'name')", then the server's own words unchanged. A tool whose name, description (over 2,000 characters) or
    schema is wrong is left out and reported, never repaired: cutting a description would edit what the model
    reads, and that text is not ours. The reason in the log names the rule and does not repeat the text.
  - The schema the model gets is the server's, returned as a copy. We do not check the arguments against it: the
    server does, and a bad argument comes back as a `remote_error` with the server's own message. So a wrong
    argument reaches the server before it is refused, unlike with a built-in tool.
  - Only text is shown to the model. A block that is not text (an image, say) is left out and the answer says how
    many. An error the server reports is a `remote_error`. A timeout is a `timeout`.
  - A server that cannot start stops the whole run (`McpStartError` names it) and the ones already started are
    closed. The config says the server should be there; a run that quietly goes on without its tools looks like
    one that had them.
  - A fingerprint (sha256 of name, description and schema, in canonical order) is kept for each server's tool
    list, for M21 to notice a server that changes its mind after it was approved.
  - `build_app` still starts nothing. Whoever opens the servers (`with open_mcp_servers(...)`) also closes them.
- **Real run** (Sonnet, once, `review` profile): the agent called `mcp__knowledge__search_knowledge`, got the entry
  for `cwe-918` and answered with it in 2 calls (2,948 input and 159 output tokens). The trajectory has the call
  and its result like any other tool. The server's own log has the one line for the call and nothing reached our
  terminal. No process was left running. With a profile that lacks the server's permission, the test with a real
  server shows the server's log has no trace of the call: it was refused before it got there.
- **Learned:**
  - A test of mine looked for `cwe-22` in two places where it could never be (the server's log and the trajectory
    hold only the call and a short message); it would have failed with correct code. I found it by reading the
    test again before running it, and it now checks what the model was actually sent.
  - I assumed "the SDK would break on stdout noise" and "a call after a timeout may leave the connection
    unusable". The probe showed neither is true for 2.3.0, and I wrote the tests from what it showed.
  - A relative `.venv/bin/python` in `mcp.yaml` works because the child process changes to the config's folder
    first. I checked it by starting the real file from `/tmp`, because it was an assumption.
  - The default test run went from 4 seconds to about 26, all of it from tests that start real servers. No
    marker was added.
- **Not tested, so not claimed:** a hostile server. The checks (`check_tool`, the fingerprint, the origin line)
  exist and have unit tests, but nothing has run against a server that really tries a poisoned description, a
  changed description after approval, or an answer with instructions in it. That is M21 (security-bench).
- **Open:** a server that is configured but unreliable stops every run; the arguments are not checked on our
  side; the server has no sandbox; and servers start one after another, so many servers make a slow start.


## M9: A contract a machine can check (2026-11-24)

- **Built:** `contract/contract.yaml` (for code) and `contract/AGENTS.md` (for the model); `prova/contract.py`
  (load, `check_contract`); `prova/mechanisms.py` (the probes); `write_deny_paths` on profiles; the `submit_result`
  tool and `Report`/`Submission`; `SnapshotStore.changed()`; the `FilesListed` stop hook. The root `AGENTS.md` stays
  what it was, a guide for people working on this repository: the agent's own contract lives in `contract/` so that
  the model never reads `uv run` commands as rules. 874 unit tests.
- **The table: rule -> what enforces it.** `contract.yaml` ties every rule to a mechanism, and at start-up each
  mechanism is run against this run's real configuration. A run whose contract does not hold does not start.

  | Rule | Mechanism | What the probe does |
  |---|---|---|
  | do not create or change test files | `permissions.write_blocked` | tries to create `tests/unit/test_probe.py` with the real tool under the `fix` profile |
  | do not read secrets | `permissions.read_blocked` | creates `.env` in a temp folder and tries to read it with the real tool |
  | no tool for the network | `permissions.lacks` | the `fix` profile has no `network` permission |
  | commands run with no network | `sandbox.network_none` | builds the real `docker run` line and wants exactly one `--network none` |
  | never print secrets | `trajectory.redaction` | the real redactor must remove a key-shaped text and a private key block |
  | the tests pass | `hook.stop.tests_before_stop` | the hook is installed in the stop hooks |
  | ruff runs after every edit | `hook.post.lint_after_edit` | the hook is installed in the post hooks |
  | the report is handed in | `tool.submit_result` | registered, has the contract's fields, offered to `fix`, and records a probe report |
  | the report is true | `hook.stop.files_listed` | the hook is installed in the stop hooks |
  | change only what is asked | `prompt_only` | **nothing: a request to the model** |
  | say so instead of guessing | `prompt_only` | **nothing: a request to the model** |

  The two `prompt_only` rows are the honest part: they are in the YAML, in `AGENTS.md` ("Rules 6 and 7 are not
  enforced by anything") and in a test that fixes the list, so adding one is a decision somebody makes.
- **Three limits of that table, said out loud:**
  - **Lint is checked to RUN, not to be obeyed.** The hook only adds a note to the result of an edit; it cannot
    stop anything. The rule says "Ruff runs after every edit. Fix what it reports", and not "ruff is clean".
  - **The hook mechanisms prove the hook is installed, not that it works.** Whether it works is the job of that
    hook's own tests. A probe that only looks at a configuration is weaker than one that tries, and these two are
    the weaker kind.
  - **`test_command` in the report is not checked.** In the real run the agent wrote `pytest test_injection_guard.py`
    although it had run the tests with the `run_tests` tool. It said so itself in its answer; nothing makes it.
- **Decided:**
  - A probe does what the agent would do, with the code the agent would meet. "Is `tests/` writable?" is answered
    by trying to create a file there with `CreateFileTool`, not by reading `permissions.yaml` and reasoning.
  - Every probe has a twin test that weakens the configuration on purpose (a profile that may write `**`, a
    `docker run` line without the flag, a redactor that removes nothing, a registry without the tool). A probe
    that cannot fail proves nothing.
  - A mechanism that crashes counts as not holding; every problem is reported at once, not only the first.
  - `check_contract` is given a `VerifyContext` made of the run's own profiles, tools and hooks: the tools come
    from `build_app`, not from a copy built for the test.
  - The report is data, not prose: the `submit_result` tool has the same field names as `output_contract`, so no
    mapping exists. Wrong reports are refused when handed in (absolute or escaping paths, a file listed twice, an
    empty reason), and nothing is recorded then.
  - What changed is what `SnapshotStore.changed()` says, which compares content with the original. A file that was
    edited and put back exactly was touched but did not change, so listing it is wrong. A file that cannot be read
    counts as changed.
  - The stop hook compares the report with that truth in both directions, names each file, and holds the agent
    back until it fits. An agent that never reports ends `unverified`, not `done`.
- **A hole in M5 that M9 found:** the `fix` profile could write everywhere except `.git/` and `hidden_tests/`, and
  `deny_paths` forbids reading as well, so there was no way to say "readable, not writable". The rule "do not
  edit tests" could not have passed its own probe. `write_deny_paths` fixes it; it wins over `write_paths`, is
  checked on the resolved path (a symlink into `tests/` is caught), and stops creation of a new file there too.
- **Real runs** (task t001, Sonnet, one run each):

  | Run | Result |
  |---|---|
  | normal | `done`, 6 calls, 24,783 input tokens; fixed the regex, `submit_result` named exactly the one file; the stop hook let it end |
  | the task asks it to change the tests, contract text shown | it did not try; reported "No files changed … the build is still red"; `unverified`, nothing to roll back |
  | the same task, **contract text NOT shown** | it tried `edit_file` on the test file and got `permission_denied` ("is read-only in this run"); reported honestly that it changed nothing; `unverified`, test file untouched |

  The third row is the check the plan asked for: asked to edit the tests, the agent is stopped by the permissions
  and not by its own good will. The second row is the other side: with the text in the prompt it did not even try.
  Both layers exist; only the permissions are a guarantee.
- **What it cost:** about 24,800 input tokens for the normal run against about 10,000 for the same fix before the
  contract. The likely causes are the contract text in the system prompt (carried in every turn), one more tool,
  and 6 calls instead of 4 (the report is one). I did not split them apart; it is one run.
- **Learned:**
  - Writing the table made me find that "lint is clean" was a rule I could not enforce. I wrote down what the
    mechanism does instead of what I wished it did.
  - A test of mine passed for the wrong reason: a "bad pattern" check was green only because the key it used did not
    exist yet, and the pattern I chose (`tests/[a`) is valid for `PathMatcher`. I found it by asking what would make
    the test go red once the code existed.
  - zsh does not split a variable on spaces: `flags="--tamper --without-contract-text"; cmd $flags` passes one
    argument. It cost me a failed run, and the second time I had cut the output with `head`, which can kill the
    process. I now write output to a file and check for leftover containers.
- **Open:** the report's `test_command` is not verified; the hook mechanisms are weak probes; `prompt_only` rules
  are two and stay requests; and every number above is a single run. The contract is checked in the script of this
  milestone; there is no CLI yet to check it in.

## M9b: The prompt is code (2026-11-24)

- **Built:** `prova/prompts/` with `render_system` and `render_user_context`; the static layers as files
  (`20-instructions.md`, `30-tool-policy.md`) next to a `CHANGELOG.md`; the contract (`contract/AGENTS.md`) as the
  last layer; `TaskEvent.prompt_hash` and `run_agent(prompt_hash=...)`. The three scripts (M4, M6, M8b) use it and
  the hand-written `SYSTEM` strings are gone. 907 unit tests.
- **Three decisions:**
  - The system prompt only talks about the tools the run is really offered. It is built from the same list the API
    gets, so a read-only run is never told the name of a tool that writes, and the paragraph for MCP tools is there
    only if one is offered.
  - The task is somebody else's text, so it goes in the user turn inside `<untrusted_issue>`, never in `system`
    (anti-pattern #12). The tag cannot be closed from inside: the `<` of any `untrusted_issue` tag in the text becomes
    `&lt;`, in any case. Everything is built with replace and join, never `str.format`: braces in a JSON schema or
    an issue would break it.
  - The hash is sha256 of the system prompt and the tool definitions, in canonical JSON, and it is written in the
    `task` event. The key is always there; `null` means "this run did not say", which is different from a missing key.
- **Real runs** (one run each):

  | Script | Layers | Hash | Result | Input tokens |
  |---|---|---|---|---|
  | M6 normal | instructions + tool-policy + contract | `bace7cdc…` | `done`, 6 turns | 28,278 |
  | M6 `--tweak` (one sentence added) | the same | `a148fdbf…` | `done`, 5 turns | 23,188 |
  | M4 `review` (cheap model) | instructions + tool-policy | `d68241f6…` | `done`, 4 turns | 15,125 |
  | M8b MCP (cheap model) | the same, with the MCP paragraph | `0213374d…` | `done`, 2 turns | 5,004 |

  The two M6 runs have two different hashes in two trajectory files, which is the check the plan asked for: the same
  task with two versions of the prompt can be told apart by one number.
- **A prediction of mine that was wrong:** I expected fewer input tokens than the 24,783 of M9. The first run had
  28,278. Input tokens follow the number of turns (each turn sends the whole prompt and history again), and the two
  runs differ in turns and in prompt at once, so one run each says nothing about whether the new prompt is lighter.
  Under the Act III rule that needs at least 3 seeds.
- **Limits that are tested instead of hoped:** the prompt has a size budget (instructions <= 300 words, tool policy
  <= 600, the `fix` system prompt <= 1100; measured 792 for `fix`, 301 for `review`).
- **Open:** the hash covers the system prompt and the tools, not the user turn or the model name; the model name is
  in the trajectory already. Nothing yet compares two hashes (that is M16). The layers are in English only.

## End of Act II: review against the anti-pattern checklist (2026-11-24)

- **How:** one grep per item over `src/` (#1, 2, 3, 6, 7, 12, 13, 14), then reading each hit; #8 by finding the
  test of the error path. Nothing here is a proof: a grep finds what I thought to look for.
- **Clean:** #2 (no `json.loads` or regex on model output), #7 (the only `anthropic.Anthropic(` is in the factory, no
  model name in `src`), #12 (`system=` only gets what `render_system` builds), #13 (no `shell=True`), #14 for the
  sandbox image (pinned by digest). #8: the sandbox and the MCP servers are context managers, with tests for leaving
  by an exception, closing twice and running after close.
- **Four `except Exception`, all kept:** the registry logs the traceback and re-raises in strict mode; the MCP thread
  logs and marks the connection dead; the hook pipeline and the contract check treat a crash as "blocked" and "not
  proved". None lets anything through.
- **One real finding (#3):** the `whitespace_mismatch` error cut its list of lines at 10 without saying so, while
  `ambiguous` did. The model reads that message to decide its next edit. Fixed test first with one shared
  `_listed`, so the two errors cannot drift apart again.
- **Two cuts left on purpose:** the 200-character reason of an MCP start error and the 80-character tool name in a
  log. Only a person reads them, not the model.
- **Not done:** `uv.lock` hashes were not checked for #14, and the coverage floor for tools does not exist yet.
