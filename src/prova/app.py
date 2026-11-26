"""Composition root: the one place where Prova's parts are created and wired together."""

from collections.abc import Sequence
from dataclasses import dataclass

from prova.config import Settings
from prova.llm.client import LLMClient, make_client
from prova.registry import Registry
from prova.tools.base import Tool
from prova.tools.edit import CreateFileTool, EditFileTool
from prova.tools.fs import ListDirTool, ReadFileTool
from prova.tools.registry import ToolRegistry
from prova.tools.report import SubmitResultTool
from prova.tools.run_tests import RunTestsTool
from prova.tools.search import SearchCodeTool


@dataclass(frozen=True, slots=True)
class App:
    settings: Settings
    llm: LLMClient
    tools: ToolRegistry
    # TODO(M7): sandbox. TODO(M8): hooks. TODO(M8b): mcp. TODO(S1): kb. TODO(S3): scanners.


def build_app(settings: Settings, *, llm: LLMClient | None = None, extra_tools: Sequence[Tool] = ()) -> App:
    """Use the given `llm` (tests pass a fake) or build the real client, register the tools, wire the App.

    `extra_tools` are tools that come from outside and are already alive, such as those of MCP servers
    (see prova.mcp.servers). They are registered next to the built-in ones; a name taken twice is an error.
    This function starts nothing: whoever starts the servers also stops them.
    """
    return App(
        settings=settings,
        llm=llm if llm is not None else make_client(settings),
        tools=_build_tools(extra_tools),
    )


def _build_tools(extra_tools: Sequence[Tool]) -> ToolRegistry:
    """Register every tool explicitly (never at import time), then freeze: nothing can be added afterwards."""
    all_tools: list[Tool] = [
        CreateFileTool(),
        EditFileTool(),
        ListDirTool(),
        ReadFileTool(),
        RunTestsTool(),
        SearchCodeTool(),
        SubmitResultTool(),
        *extra_tools,
    ]
    registry = Registry[Tool]("tool")
    for tool in all_tools:
        registry.register(tool)
    registry.freeze()
    return ToolRegistry(registry)
