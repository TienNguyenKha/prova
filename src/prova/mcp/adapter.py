"""McpToolAdapter: a tool of an MCP server, as one more tool of Prova.

Once adapted it goes through the same registry, the same permission check, the same hooks and the same
trajectory as a tool written here. Nothing in the loop knows the difference.

What the server says about its tools is somebody else's text. Whatever fails prova.mcp.tools.check_tool is
left out and reported; whatever passes is shown to the model as it is, with a line in front that says where
it comes from.
"""

import copy
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import mcp.types as types
from pydantic import BaseModel, ConfigDict

from prova.errors import ErrorCode, McpConnectionError, McpTimeoutError, ToolError
from prova.harness.permissions import Permission
from prova.mcp.config import McpConfig
from prova.mcp.tools import check_tool, namespaced_name, result_to_tool_result
from prova.tools.base import RunContext, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_RESULT_CHARS = 20_000  # how much of one answer of a server the model is shown


class McpServerLike(Protocol):
    """What the adapter needs of a connection. prova.mcp.connection.McpConnection is one."""

    @property
    def tools(self) -> tuple[types.Tool, ...]: ...

    @property
    def tools_hash(self) -> str: ...

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout_s: float | None = None
    ) -> types.CallToolResult: ...


class RawArguments(BaseModel):
    """The arguments of a tool whose schema is the server's, not ours. Any field is accepted and passed on:
    the server checks them against its own schema and says what is wrong (a `remote_error` for the model)."""

    model_config = ConfigDict(extra="allow")
    server_schema: ClassVar[Mapping[str, Any]] = {}

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """The schema the server gave, as it was. A copy: nobody can change what the server said."""
        return copy.deepcopy(dict(cls.server_schema))


def make_arguments_model(schema: Mapping[str, Any]) -> type[RawArguments]:
    frozen = copy.deepcopy(dict(schema))

    class Arguments(RawArguments):
        server_schema: ClassVar[Mapping[str, Any]] = frozen

    return Arguments


class McpToolAdapter:
    def __init__(
        self,
        server: str,
        tool: types.Tool,
        connection: McpServerLike,
        permission: Permission,
        *,
        max_result_chars: int = DEFAULT_RESULT_CHARS,
    ) -> None:
        self._tool_name = tool.name  # the name the server knows; `name` below is the one the model sees
        self._connection = connection
        self._max_result_chars = max_result_chars
        self.name = namespaced_name(server, tool.name)
        origin = f"(a tool of the external MCP server {server!r})"
        self.description = f"{origin} {tool.description or f'The tool {tool.name!r}.'}"
        self.args_model = make_arguments_model(tool.input_schema)
        self.permission = permission

    def run(self, args: Any, ctx: RunContext) -> ToolResult:
        try:
            result = self._connection.call_tool(self._tool_name, args.model_dump())
        except McpTimeoutError as e:
            raise ToolError(ErrorCode.TIMEOUT, str(e)) from None
        except McpConnectionError as e:  # the server died, refused the call, or is not there
            raise ToolError(ErrorCode.REMOTE_ERROR, str(e)) from None
        return result_to_tool_result(result, max_chars=self._max_result_chars)


@dataclass(frozen=True, slots=True)
class SkippedTool:
    server: str
    tool: str
    reason: str  # the rule that was broken; never the server's own text


@dataclass(frozen=True, slots=True)
class Discovery:
    adapters: tuple[McpToolAdapter, ...]
    skipped: tuple[SkippedTool, ...]
    hashes: Mapping[str, str]  # a fingerprint of each server's tool list, for noticing a server that changes


def discover_tools(
    config: McpConfig,
    connections: Mapping[str, McpServerLike],
    *,
    max_result_chars: int = DEFAULT_RESULT_CHARS,
) -> Discovery:
    """Adapt the tools of every connection. A tool that does not pass the checks, or is listed twice, is left
    out and reported; the rest are kept. Each server's tools get the permission written for it in the config.
    """
    adapters: list[McpToolAdapter] = []
    skipped: list[SkippedTool] = []
    hashes: dict[str, str] = {}
    for server, connection in connections.items():
        if server not in config.servers:
            raise ValueError(f"the connection {server!r} has no entry in the MCP config")
        permission = config.servers[server].permission
        hashes[server] = connection.tools_hash
        seen: set[str] = set()
        for tool in connection.tools:
            reason = "the server lists this tool twice" if tool.name in seen else check_tool(server, tool)
            seen.add(tool.name)
            if reason is not None:
                skipped.append(SkippedTool(server, tool.name, reason))
                shown = tool.name[:80]  # the name is the server's text too
                logger.warning("MCP tool left out: server=%r tool=%r reason: %s", server, shown, reason)
                continue
            adapter = McpToolAdapter(server, tool, connection, permission, max_result_chars=max_result_chars)
            adapters.append(adapter)
    return Discovery(tuple(adapters), tuple(skipped), hashes)
