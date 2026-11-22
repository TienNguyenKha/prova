"""M0 step 4: provoke SDK errors on purpose. Record exception type, HTTP status and whether the SDK retries.

Logging is set to INFO so every HTTP attempt and every "Retrying request" line is visible.
Case 5 uses an unreachable local port, so it costs nothing.
"""

import logging
import time
from typing import Any

import anthropic

from prova.config import load_settings


def attempt(label: str, client: anthropic.Anthropic, model: str, **overrides: Any) -> None:
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Say hi."}],
        **overrides,
    }
    print(f"\n=== {label}")
    started = time.perf_counter()
    try:
        client.messages.create(**params)
        print("no error (unexpected)")
    except anthropic.APIError as e:
        chain = " > ".join(cls.__name__ for cls in type(e).__mro__[:-3])  # up to AnthropicError
        print(f"exception : {type(e).__name__}")
        print(f"hierarchy : {chain}")
        print(f"status    : {getattr(e, 'status_code', None)}")
        print(f"message   : {e.message}")
        print(f"elapsed   : {time.perf_counter() - started:.2f}s")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="  [log] %(name)s: %(message)s")
    settings = load_settings()
    key = settings.anthropic_api_key.get_secret_value()
    good = anthropic.Anthropic(api_key=key, timeout=settings.request_timeout_s)
    bad_key = anthropic.Anthropic(api_key="sk-ant-this-is-not-a-real-key", timeout=settings.request_timeout_s)
    offline = anthropic.Anthropic(api_key=key, base_url="http://127.0.0.1:9", timeout=5)

    attempt("1. wrong model name", good, "claude-does-not-exist")
    attempt("2. invalid API key", bad_key, settings.model)
    attempt("3. max_tokens=-1", good, settings.model, max_tokens=-1)
    attempt("4. empty message", good, settings.model, messages=[{"role": "user", "content": ""}])
    attempt("5. connection refused (offline)", offline, settings.model)


if __name__ == "__main__":
    main()
