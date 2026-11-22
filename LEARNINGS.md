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
