# Changelog of the prompt

Every change to a file of the prompt is written here with the reason. The hash of the rendered prompt (see
`prova.prompts.render`) goes into every trajectory, so a result can always be tied to the prompt it ran with.
From Act III on, a change also records the benchmark result before and after.

## 2026-11-26: the prompts of a task are templates in `tasks/`, with their hash in the config

- `tasks/fix_goal.md`, `tasks/fix_feedback.md` (the fix loop of M13), `tasks/repro.md` (M13b), `tasks/adversary.md`
  and `tasks/self_review.md` (M14): the words a run is given after the issue were strings in the code, outside the
  prompt hash. The words are the same, letter for letter; only where they live changed. They are filled in one
  pass (a value is never read again) and the hash of each template goes into the config of the agents that use it
  (`task_prompts`), so a result says which words its runs were given. Benchmark: not measured, no word changed.

## 2026-11-26: rule 2 of the contract, reproduction tests are not to be changed (M13b)

- `contract/AGENTS.md`: a new rule 2, "Do not change the reproduction tests in repro/. Make them pass by fixing
  the code." It is enforced by the `fix` profile (`repro/**` is in its write deny-list), like rule 1; the rules
  after it move down by one. Benchmark: not measured, the change only adds a rule the permissions already enforce.
  The cassette of the replay test is recorded again, since the system prompt is part of every request.

## 2026-11-24: the first version, in layers

- `20-instructions.md`, `30-tool-policy.md`: the prompt was a string typed into each script, and it never said how
  to treat the task text. Now the layers are files, the tool paragraphs are chosen by the tools a run is really
  given, and the task is told to be data.
- The contract (`contract/AGENTS.md`) is the last layer as it is: one source of truth for the rules.
- No few-shot examples. They are added only when the agent repeats the same mistake, with the measurement that
  shows it.
