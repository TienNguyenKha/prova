"""M2 step 5: break the tool-use rules on purpose and record how the API reacts.

Run by hand (about 9 small calls): `uv run python experiments/act1/m2_break_rules.py`

One real call gets a genuine assistant turn with two tool_use blocks. Then each experiment re-sends a
deliberately damaged version of the conversation. The API either rejects it (the message tells us the rule)
or accepts it (the rule is looser than we thought). Both outcomes are results.
"""

import copy
from typing import Any, cast

import anthropic

from prova.app import build_app
from prova.config import load_settings
from prova.tools.base import Permission, RunContext

TASK = "Compare the version in notes.txt with the app_version in config.txt. Are they the same?"


def send(client: anthropic.Anthropic, model: str, label: str, tools: list[Any], messages: list[Any]) -> None:
    print(f"\n=== {label}")
    try:
        response = client.messages.create(model=model, max_tokens=256, tools=tools, messages=messages)
    except anthropic.APIStatusError as e:
        print(f"REJECTED  {type(e).__name__} (HTTP {e.status_code})\n  {e.message}")
    else:
        reply = "".join(block.text for block in response.content if block.type == "text")
        print(f"ACCEPTED  stop_reason={response.stop_reason}")
        print(f"  reply: {reply[:300]!r}")


def main() -> None:
    settings = load_settings()
    model = settings.model
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    # The raw SDK client probes the API directly (a wrapped client would hide its errors behind LLMError).
    # The app is only used for its tools. Only read_file is offered, so the model reads both files itself.
    app = build_app(settings, llm=client)
    tools: list[Any] = [t for t in app.tools.to_api_tools({Permission.READ}) if t["name"] == "read_file"]
    ctx = RunContext(workspace=settings.project_root / "experiments" / "act1" / "sandbox")

    # A genuine first turn: the model asks to read both files.
    user: dict[str, Any] = {"role": "user", "content": TASK}
    first = client.messages.create(model=model, max_tokens=1024, tools=tools, messages=[user])
    blocks: list[dict[str, Any]] = [
        cast(dict[str, Any], app.tools.dispatch(b.name, b.input, ctx).to_tool_result_block(b.id))
        for b in first.content
        if b.type == "tool_use"
    ]
    if len(blocks) != 2:
        raise SystemExit("The model did not call two tools in one turn; run the script again.")
    assistant: dict[str, Any] = {"role": "assistant", "content": first.content}
    print(f"genuine turn: tool_use ids = {[b['tool_use_id'] for b in blocks]}")

    def user_with(*result_blocks: dict[str, Any]) -> dict[str, Any]:
        return {"role": "user", "content": list(result_blocks)}

    def attempt(label: str, messages: list[Any]) -> None:
        send(client, model, label, tools, messages)

    attempt("0. control: everything correct", [user, assistant, user_with(*blocks)])

    wrong_id = copy.deepcopy(blocks)
    wrong_id[0]["tool_use_id"] = "toolu_does_not_exist"
    attempt("1. wrong tool_use_id", [user, assistant, user_with(*wrong_id)])

    text_only = {"role": "assistant", "content": [{"type": "text", "text": "Let me check both files."}]}
    attempt("2. tool_use blocks removed from the assistant turn", [user, text_only, user_with(*blocks)])

    attempt("3. tool_result with no tool_use anywhere before it", [user_with(*blocks)])

    attempt(
        "4. the two tool_results split into two user messages",
        [user, assistant, user_with(blocks[0]), user_with(blocks[1])],
    )

    attempt("5. (bonus) only one of the two tool_results sent", [user, assistant, user_with(blocks[0])])

    # Step 6 follow-up: the same error text with and without is_error.
    failed = copy.deepcopy(blocks)
    failed[0]["content"] = "not_found: no such file: notes.txt"
    failed[0]["is_error"] = True
    attempt("6a. error reported with is_error=True", [user, assistant, user_with(*failed)])
    del failed[0]["is_error"]
    attempt("6b. same error text WITHOUT is_error", [user, assistant, user_with(*failed)])


if __name__ == "__main__":
    main()
