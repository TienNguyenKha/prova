"""M3 step 3: does forcing `tool_choice` (method B: a fake tool plus a forced call) work on current models?

Run by hand (6 small calls, 2 models x 3 settings): `uv run python experiments/act1/m3_tool_choice_probe.py`

The same tool is sent each time; only `tool_choice` changes. `auto` is the control: if it works, tools in
general are fine and any failure comes from forcing the choice.
"""

from typing import Any

import anthropic

from prova.config import load_settings
from prova.structured import BugClassification

TOOL: dict[str, Any] = {
    "name": "report_bug",
    "description": "Report the classification of the code snippet.",
    "input_schema": BugClassification.model_json_schema(),
}
CHOICES: list[tuple[str, dict[str, str]]] = [
    ("auto (control)", {"type": "auto"}),
    ("any (must call some tool)", {"type": "any"}),
    ("tool (must call report_bug)", {"type": "tool", "name": "report_bug"}),
]


def probe(
    client: anthropic.Anthropic, model: str, label: str, tool_choice: dict[str, str], code: str
) -> None:
    print(f"\n--- {model} | tool_choice = {label}")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=512,
            system="Classify the bug in the code you are given by calling the report_bug tool.",
            messages=[{"role": "user", "content": f"<code_to_classify>\n{code}\n</code_to_classify>"}],
            tools=[TOOL],
            tool_choice=tool_choice,
        )
    except anthropic.APIStatusError as e:
        print(f"REJECTED  {type(e).__name__} (HTTP {e.status_code})\n  {e.message}")
        return
    kinds = [block.type for block in response.content]
    print(f"ACCEPTED  stop_reason={response.stop_reason} blocks={kinds}")
    for block in response.content:
        if block.type == "tool_use":
            print(f"  tool_use input: {block.input}")


def main() -> None:
    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    code = (settings.project_root / "experiments" / "act1" / "m3_cases" / "c01_t001.py").read_text()

    for model in (settings.model, settings.cheap_model):
        for label, tool_choice in CHOICES:
            probe(client, model, label, tool_choice, code)


if __name__ == "__main__":
    main()
