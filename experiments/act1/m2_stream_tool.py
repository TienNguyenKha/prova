"""M1 step 6 (deferred to M2): stream a request that makes the model call a tool, and watch input_json_delta.

Run by hand (one small call): `uv run python experiments/act1/m2_stream_tool.py`
The tool is never executed here; we only look at how the tool arguments arrive.
"""

from typing import Any

import anthropic

from prova.config import load_settings
from prova.tools.fs import FS_TOOLS

TASK = "Compare the version in notes.txt with the app_version in config.txt. Are they the same?"


def describe(event: Any) -> str:
    match event.type:
        case "content_block_start":
            block = event.content_block
            extra = f" name={block.name} id={block.id}" if block.type == "tool_use" else ""
            return f"index={event.index} type={block.type}{extra}"
        case "content_block_delta":
            delta = event.delta
            if delta.type == "input_json_delta":
                return f"input_json_delta partial_json={delta.partial_json!r}"
            return f"{delta.type} {getattr(delta, 'text', '')!r}"
        case "input_json":  # convenience event of the SDK helper: the same pieces plus a parsed snapshot
            return f"partial_json={event.partial_json!r} snapshot={event.snapshot}"
        case "content_block_stop":
            return f"index={event.index}"
        case "message_delta":
            return f"stop_reason={event.delta.stop_reason}"
        case _:
            return ""


def main() -> None:
    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    with client.messages.stream(
        model=settings.model,
        max_tokens=1024,
        tools=FS_TOOLS,
        messages=[{"role": "user", "content": TASK}],
    ) as stream:
        for event in stream:
            print(f"{event.type:22} {describe(event)}")
        final = stream.get_final_message()

    print("\nfinal tool_use blocks (complete, parsed):")
    for block in final.content:
        if block.type == "tool_use":
            print(f"  {block.name} {block.input}")


if __name__ == "__main__":
    main()
