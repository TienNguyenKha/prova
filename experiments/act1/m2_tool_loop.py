"""M2 steps 2-4, 6-7: a hand-written tool loop with read_file, printing every block of every turn.

Run by hand (a few small calls):
  uv run python experiments/act1/m2_tool_loop.py
  uv run python experiments/act1/m2_tool_loop.py "Read missing.txt and summarise it."   # step 6: tool error
  uv run python experiments/act1/m2_tool_loop.py --max-iterations 1                     # step 7: out of turns

The model can only read files under experiments/act1/sandbox/.
"""

import argparse
from pathlib import Path
from typing import Any, cast

import anthropic
from anthropic.types import Message, MessageParam

from prova.config import load_settings
from prova.llm.tool_messages import build_tool_results
from prova.tools.fs import FS_TOOLS, make_fs_runner

DEFAULT_TASK = "Compare the version in notes.txt with the app_version in config.txt. Are they the same?"


def preview(text: str, limit: int = 160) -> str:
    one_line = text.replace("\n", "\\n")
    return one_line if len(one_line) <= limit else one_line[:limit] + "..."


def show_assistant(turn: int, response: Message) -> None:
    usage = response.usage
    print(f"\n--- turn {turn}: assistant (stop_reason={response.stop_reason})")
    print(f"    usage: input={usage.input_tokens} output={usage.output_tokens}")
    for block in response.content:
        if block.type == "text":
            print(f"  [text]        {preview(block.text)}")
        elif block.type == "tool_use":
            print(f"  [tool_use]    id={block.id} name={block.name} input={block.input}")
        else:
            print(f"  [{block.type}]")


def show_results(message: MessageParam) -> None:
    print("--- user message with tool results")
    for block in cast(list[dict[str, Any]], message["content"]):
        flag = " is_error=True" if block.get("is_error") else ""
        print(f"  [tool_result] tool_use_id={block['tool_use_id']}{flag}")
        print(f"                {preview(str(block['content']))}")


def run_tool_loop(
    client: anthropic.Anthropic, model: str, task: str, *, root: Path, max_iterations: int
) -> list[MessageParam]:
    """Call the API; while the model asks for tools, run them and send the results back."""
    run = make_fs_runner(root)
    messages: list[MessageParam] = [{"role": "user", "content": task}]
    for turn in range(1, max_iterations + 1):
        response = client.messages.create(model=model, max_tokens=1024, tools=FS_TOOLS, messages=messages)
        show_assistant(turn, response)
        # Keep EVERY block, tool_use included: each tool_result must match a tool_use just before it.
        messages.append({"role": "assistant", "content": response.content})
        results = build_tool_results(response.content, run)
        if results is None:  # no tool_use: the model is done
            return messages
        show_results(results)
        messages.append(results)
    print(f"\n!!! stopped after max_iterations={max_iterations}: the model was still asking for tools")
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description="M2 tool loop experiment")
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK)
    parser.add_argument("--max-iterations", type=int, default=8)
    args = parser.parse_args()

    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    root = settings.project_root / "experiments" / "act1" / "sandbox"

    print(f"task: {args.task}")
    messages = run_tool_loop(client, settings.model, args.task, root=root, max_iterations=args.max_iterations)
    print(f"\nconversation has {len(messages)} messages")


if __name__ == "__main__":
    main()
