"""M1 steps 4-5: stream one answer, print the events, and measure TTFT against total time.

Run by hand (6 small calls: 3 per model): `uv run python experiments/act1/m1_stream.py`
"""

import time
from statistics import mean
from typing import Any

import anthropic

from prova.config import Settings, load_settings

PROMPT = "Giải thích HTTP request là gì, khoảng 150 từ."
MAX_DELTAS_SHOWN = 3  # a real answer has hundreds of delta events; show only the first few


def describe(event: Any) -> str:
    """One short line of detail per event type."""
    match event.type:
        case "message_start":
            return f"id={event.message.id} input_tokens={event.message.usage.input_tokens}"
        case "content_block_start":
            return f"block type={event.content_block.type}"
        case "content_block_delta":
            return f"{event.delta.type} {getattr(event.delta, 'text', '')!r}"
        case "message_delta":
            return f"stop_reason={event.delta.stop_reason} output_tokens={event.usage.output_tokens}"
        case "text":  # a convenience event added by the SDK helper, not part of the raw protocol
            return f"{event.text!r}"
        case _:
            return ""


def stream_once(client: anthropic.Anthropic, model: str, *, trace: bool) -> tuple[float, float, int]:
    """Return (ttft_s, total_s, output_tokens) for one streamed answer."""
    started = time.perf_counter()
    ttft: float | None = None
    deltas = 0
    with client.messages.stream(
        model=model, max_tokens=600, messages=[{"role": "user", "content": PROMPT}]
    ) as stream:
        for event in stream:
            elapsed = time.perf_counter() - started
            if event.type == "content_block_delta":
                deltas += 1
                if ttft is None:
                    ttft = elapsed  # first piece of the answer reaches us
            hidden = event.type in ("content_block_delta", "text") and deltas > MAX_DELTAS_SHOWN
            if trace and not hidden:
                print(f"  {elapsed:6.3f}s  {event.type:20} {describe(event)}")
        final = stream.get_final_message()
    total = time.perf_counter() - started
    if trace:
        print(f"  ... {deltas} content_block_delta events in total ({MAX_DELTAS_SHOWN} shown)")
    if ttft is None:
        raise RuntimeError("no content_block_delta received")
    return ttft, total, final.usage.output_tokens


def measure(client: anthropic.Anthropic, model: str, runs: int = 3) -> None:
    print(f"\n=== {model}")
    results = []
    for i in range(runs):
        ttft, total, out = stream_once(client, model, trace=(i == 0))
        results.append((ttft, total))
        print(
            f"run {i + 1}: ttft={ttft:.2f}s total={total:.2f}s "
            f"ttft/total={ttft / total:.0%} output_tokens={out}"
        )
    mean_ttft = mean(r[0] for r in results)
    mean_total = mean(r[1] for r in results)
    print(f"mean: ttft={mean_ttft:.2f}s total={mean_total:.2f}s ttft/total={mean_ttft / mean_total:.0%}")


def main() -> None:
    settings: Settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    measure(client, settings.cheap_model)
    measure(client, settings.model)


if __name__ == "__main__":
    main()
