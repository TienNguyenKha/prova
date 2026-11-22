"""M3: what does method A (prompt "ONLY JSON") return on the cases that always failed on the main model?

Run by hand (4 small calls): `uv run python experiments/act1/m3_peek_failures.py`

m3_compare.py grades replies without keeping them. Here the start of each raw reply is printed, so we can see
whether a bad reply is a markdown fence, prose or something else. (Imports from m3_compare, same directory.)
"""

import anthropic
from m3_compare import JSON_SYSTEM, load_cases

from prova.config import load_settings

INVALID_JSON_CASES = ("c03", "c13", "c17")  # failed 3 times out of 3
EXTRA_FIELD_CASES = ("c06",)  # failed 3 times out of 3


def main() -> None:
    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    cases = dict(load_cases(settings.project_root / "experiments" / "act1" / "m3_cases"))

    for case_id in (*INVALID_JSON_CASES, *EXTRA_FIELD_CASES):
        response = client.messages.create(
            model=settings.model,
            max_tokens=settings.max_tokens,
            system=JSON_SYSTEM,
            messages=[{"role": "user", "content": cases[case_id]}],
        )
        blocks = [block.type for block in response.content]
        text = "".join(block.text for block in response.content if block.type == "text").lstrip()
        print(f"\n=== {case_id} | stop_reason={response.stop_reason} | blocks={blocks}")
        print(f"starts with '{{': {text.startswith('{')} | starts with a fence: {text.startswith('```')}")
        print(f"first 400 chars: {text[:400]!r}")


if __name__ == "__main__":
    main()
