"""M2 follow-up: does the API accept two consecutive messages with the same role? (tests the "merge" guess)

Run by hand (2 tiny calls): `uv run python experiments/act1/m2_same_role.py`

In m2_break_rules.py #4, two user messages holding one tool_result each were accepted. Guess: the API merges
consecutive messages of the same role before validating them. Here: the same idea with plain text, no tools.
"""

from typing import Any

import anthropic

from prova.config import load_settings


def send(client: anthropic.Anthropic, model: str, label: str, messages: list[Any]) -> None:
    print(f"\n=== {label}")
    try:
        response = client.messages.create(model=model, max_tokens=100, messages=messages)
    except anthropic.APIStatusError as e:
        print(f"REJECTED  {type(e).__name__} (HTTP {e.status_code})\n  {e.message}")
        return
    reply = "".join(block.text for block in response.content if block.type == "text")
    print(f"ACCEPTED  stop_reason={response.stop_reason} input_tokens={response.usage.input_tokens}")
    print(f"  reply: {reply[:200]!r}")


def main() -> None:
    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    model = settings.cheap_model

    # One message with both sentences, as the reference for what the model sees and what it costs.
    merged = [{"role": "user", "content": "My name is Linh.\nWhat is my name? Answer in one word."}]
    send(client, model, "A. one user message (reference)", merged)

    # The same text as two consecutive user messages.
    split = [
        {"role": "user", "content": "My name is Linh."},
        {"role": "user", "content": "What is my name? Answer in one word."},
    ]
    send(client, model, "B. two consecutive user messages", split)


if __name__ == "__main__":
    main()
