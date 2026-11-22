"""M0 step 2: change one parameter at a time and watch stop_reason and usage. Run by hand (4 tiny calls)."""

from typing import Any

import anthropic

from prova.config import Settings, load_settings

COUNT_PROMPT = "Count from 1 to 10."
LIST_PROMPT = "Count from 1 to 10 as a numbered list, one per line, like '1. one'."


def call(client: anthropic.Anthropic, settings: Settings, label: str, prompt: str, **extra: Any) -> None:
    params: dict[str, Any] = {"max_tokens": 256, **extra}
    response = client.messages.create(
        model=settings.model,
        messages=[{"role": "user", "content": prompt}],
        **params,
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    print(f"=== {label}")
    print(f"stop_reason   : {response.stop_reason}")
    print(f"stop_sequence : {response.stop_sequence!r}")
    print(f"input_tokens  : {response.usage.input_tokens}")
    print(f"output_tokens : {response.usage.output_tokens}")
    print(f"text          : {text!r}\n")


def main() -> None:
    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )

    call(client, settings, "1. baseline", COUNT_PROMPT)
    call(client, settings, "2. max_tokens=5", COUNT_PROMPT, max_tokens=5)
    call(client, settings, "3. stop_sequences=['3.']", LIST_PROMPT, stop_sequences=["3."])
    call(client, settings, "4. with system prompt", COUNT_PROMPT, system="You are a terse assistant.")


if __name__ == "__main__":
    main()
