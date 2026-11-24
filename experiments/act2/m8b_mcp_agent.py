"""M8b: the agent uses a tool of an MCP server, through the same registry, permissions and log as the rest.

Run by hand (a few model calls):
  uv run python experiments/act2/m8b_mcp_agent.py
  uv run python experiments/act2/m8b_mcp_agent.py --model cheap --question "Which entry is about SQLi?"

The servers in mcp.yaml are started as child processes of this script (on this machine: there is no sandbox
around them) and stopped when it ends. The run uses the `review` profile, which can only read, so the
server's tool works because mcp.yaml gives it the `read` permission.
"""

import argparse
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prova.agent.loop import run_agent
from prova.app import build_app
from prova.config import load_settings
from prova.errors import McpStartError
from prova.harness.permissions import get_profile, load_profiles
from prova.harness.redact import Redactor
from prova.harness.trajectory import TrajectoryWriter
from prova.mcp.config import load_mcp_config
from prova.mcp.servers import open_mcp_servers
from prova.tools.base import RunContext

DEFAULT_QUESTION = (
    "Which weakness is the entry about server-side request forgery? Look it up in the knowledge base and "
    "give me its id and a one-line summary."
)
SYSTEM = (
    "You answer questions with the tools you are given. Use the tools before you answer, and say which tool "
    "the answer came from. If the tools do not have it, say so instead of guessing."
)


class UtcClock:
    def __call__(self) -> datetime:
        return datetime.now(UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description="M8b: the agent uses an MCP tool")
    parser.add_argument("--model", choices=["cheap", "main"], default="main")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--max-iterations", type=int, default=10)
    args = parser.parse_args()

    settings = load_settings()
    model = settings.cheap_model if args.model == "cheap" else settings.model
    target = settings.model_copy(update={"model": model})
    profile = get_profile(load_profiles(settings.project_root / "permissions.yaml"), "review")
    config = load_mcp_config(settings.project_root / "mcp.yaml")

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    runs = settings.project_root / "experiments" / "runs"
    log_path = runs / f"m8b-{run_id}.jsonl"
    redactor = Redactor.from_environ(os.environ)
    writer = TrajectoryWriter(log_path, run_id=run_id, clock=UtcClock(), redactor=redactor)

    server_logs = runs / f"m8b-{run_id}-servers"
    try:
        with open_mcp_servers(config, stderr_dir=server_logs) as found, tempfile.TemporaryDirectory() as tmp:
            print(f"model={model} profile=review")
            print("tools of the servers:", [a.name for a in found.adapters])
            print("left out:", [(s.server, s.tool, s.reason) for s in found.skipped] or "nothing")
            print("fingerprint of each tool list:", {k: v[:16] + "..." for k, v in found.hashes.items()})

            app = build_app(target, extra_tools=found.adapters)
            ctx = RunContext.from_profile(profile, Path(tmp), strict=True)
            print(f"question: {args.question}")
            result = run_agent(
                args.question,
                llm=app.llm,
                tools=app.tools,
                ctx=ctx,
                settings=target,
                system=SYSTEM,
                max_iterations=args.max_iterations,
                trajectory=writer,
                redactor=redactor,
            )
    except McpStartError as e:
        print(f"cannot run: {e}")
        raise SystemExit(1) from None

    print("\n=== what the agent called")
    for message in result.messages:
        content: Any = message["content"]
        if message["role"] == "assistant" and not isinstance(content, str):
            for block in content:
                if block.type == "tool_use":
                    print(f"  {block.name} {block.input}")
        elif message["role"] == "user" and not isinstance(content, str):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    flag = " ERROR" if block.get("is_error") else ""
                    print(f"    -> result{flag}: {str(block['content'])[:150]!r}")
    print(f"\n=== status={result.status} iterations={result.iterations}")
    print(f"tokens: input={result.usage.total_input_tokens} output={result.usage.total_output_tokens}")
    print(f"trajectory: {log_path}")
    print(f"\n=== final answer\n{result.final_text}")


if __name__ == "__main__":
    main()
