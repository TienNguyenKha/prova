"""M0 step 1: one real API call, print the raw response. Run by hand (it costs a few tokens)."""

import anthropic

from prova.config import load_settings


def main() -> None:
    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )

    response = client.messages.create(
        model=settings.model,
        max_tokens=256,
        messages=[{"role": "user", "content": "Say hello in one short sentence."}],
    )

    print(response.model_dump_json(indent=2))

    # Do not assume content[0] is text: it can be thinking or tool_use.
    print("\n--- content blocks ---")
    for block in response.content:
        print(block.type, "->", block.text if block.type == "text" else "(not text)")


if __name__ == "__main__":
    main()
