"""M4: run the real agent loop on a question about this very repository, and print the trace.

Run by hand (a handful of model calls):
  uv run python experiments/act2/m4_ask_repo.py                       # main model, the default question
  uv run python experiments/act2/m4_ask_repo.py --model cheap
  uv run python experiments/act2/m4_ask_repo.py --question "What does ReadFileTool do with a long file?"

The agent works on a COPY of src/, tests/ and experiments/ in a temporary directory. The real repository root
holds `.env` (the API key), and nothing stops a read tool from reading a file that is inside its workspace.
"""

import argparse
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from prova.agent.loop import AgentResult, run_agent
from prova.app import build_app
from prova.config import load_settings
from prova.tools.base import RunContext

DEFAULT_QUESTION = (
    "Where is the function `check_stop_reason` defined, and where is it used? "
    "Give file paths and line numbers."
)
SYSTEM = (
    "You answer questions about the code in the workspace. Look at the files with the tools before you "
    "answer. Name file paths and line numbers. If you cannot find something, say so instead of guessing."
)
COPIED = ("src", "tests", "experiments")


def preview(text: str, limit: int = 110) -> str:
    one_line = text.replace("\n", "\\n")
    return one_line if len(one_line) <= limit else one_line[:limit] + "..."


def grep_repo(root: Path, needle: str) -> list[str]:
    """The ground truth for the default question: a plain text search, no model involved."""
    hits = []
    for top in COPIED:
        for path in sorted((root / top).rglob("*.py")):
            if {"m3_cases", "__pycache__", "runs"} & set(path.parts):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if needle in line:
                    hits.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return hits


def show_trace(result: AgentResult) -> None:
    print("\n=== trace")
    turn = 0
    chars_in_results: list[int] = []
    for message in result.messages[1:]:  # message 0 is the task
        content: Any = message["content"]
        if message["role"] == "assistant":
            record = result.usage.records[turn]
            turn += 1
            print(
                f"\nturn {turn}: model reply (input={record.input_tokens} output={record.output_tokens} "
                f"{record.latency_s:.1f}s)"
            )
            for block in content:
                if block.type == "text":
                    print(f"  text      {preview(block.text)}")
                elif block.type == "tool_use":
                    print(f"  tool call {block.name} {block.input}")
        else:
            for block in content:
                size = len(block["content"])
                chars_in_results.append(size)
                flag = " ERROR" if block.get("is_error") else ""
                print(f"  result{flag}  {size} chars: {preview(block['content'])}")
    if chars_in_results:
        print(f"\ntool results: {len(chars_in_results)} in total, {sum(chars_in_results)} chars, "
              f"largest {max(chars_in_results)} chars")  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description="M4: ask the agent a question about this repository")
    parser.add_argument("--model", choices=["cheap", "main"], default="main")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--max-iterations", type=int, default=15)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)  # a bug inside a tool shows its traceback here
    settings = load_settings()
    model = settings.cheap_model if args.model == "cheap" else settings.model
    target = settings.model_copy(update={"model": model})
    app = build_app(target)

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        skip = shutil.ignore_patterns("__pycache__", "runs")
        for top in COPIED:
            shutil.copytree(settings.project_root / top, workspace / top, ignore=skip)
        ctx = RunContext(workspace=workspace, strict=True)  # strict: a bug in a tool stops the run loudly
        print(f"model={model}\nquestion: {args.question}")
        result = run_agent(
            args.question,
            llm=app.llm,
            tools=app.tools,
            ctx=ctx,
            settings=target,
            system=SYSTEM,
            max_iterations=args.max_iterations,
        )

    show_trace(result)
    print(f"\n=== result: status={result.status} iterations={result.iterations} detail={result.detail!r}")
    print(f"tokens: input={result.usage.total_input_tokens} output={result.usage.total_output_tokens}")
    print(f"\n=== final answer\n{result.final_text}")
    if args.question == DEFAULT_QUESTION:
        print("\n=== ground truth (plain text search, no model)")
        print("\n".join(grep_repo(settings.project_root, "check_stop_reason")))


if __name__ == "__main__":
    main()
