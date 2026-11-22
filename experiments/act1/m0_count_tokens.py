"""M0 step 3: compare messages.count_tokens with the real usage.input_tokens. Run by hand."""

from typing import Any

import anthropic

from prova.config import Settings, load_settings

CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("hello", "Say hello in one short sentence.", {}),
    ("count", "Count from 1 to 10.", {}),
    ("count + system", "Count from 1 to 10.", {"system": "You are a terse assistant."}),
]


def compare(
    client: anthropic.Anthropic, settings: Settings, label: str, prompt: str, extra: dict[str, Any]
) -> None:
    messages = [{"role": "user", "content": prompt}]
    counted = client.messages.count_tokens(model=settings.model, messages=messages, **extra).input_tokens
    actual = client.messages.create(
        model=settings.model, max_tokens=1, messages=messages, **extra
    ).usage.input_tokens
    print(f"{label:15} count_tokens={counted:3}  usage.input_tokens={actual:3}  diff={counted - actual}")


def main() -> None:
    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    for label, prompt, extra in CASES:
        compare(client, settings, label, prompt, extra)


if __name__ == "__main__":
    main()
