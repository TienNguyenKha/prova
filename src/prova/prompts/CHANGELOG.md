# Changelog of the prompt

Every change to a file of the prompt is written here with the reason. The hash of the rendered prompt (see
`prova.prompts.render`) goes into every trajectory, so a result can always be tied to the prompt it ran with.
From Act III on, a change also records the benchmark result before and after.

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
