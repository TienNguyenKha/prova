"""open_mcp_servers: start every server in mcp.yaml, hand over their tools, and stop them all afterwards.

A block of code that needs the servers writes `with open_mcp_servers(config) as found:`. Whatever happens
inside, even an exception, and whatever happens while the servers are being started, no server is left
running.
"""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from prova.mcp.adapter import DEFAULT_RESULT_CHARS, Discovery, McpServerLike, discover_tools
from prova.mcp.config import McpConfig
from prova.mcp.connection import DEFAULT_CONNECT_TIMEOUT_S, McpConnection


@contextmanager
def open_mcp_servers(
    config: McpConfig,
    *,
    stderr_dir: Path | None = None,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    max_result_chars: int = DEFAULT_RESULT_CHARS,
) -> Generator[Discovery]:
    """Start the servers of `config` and yield the tools they offer (see `discover_tools`).

    If one server cannot be started the whole thing fails with McpStartError, naming it, and the servers that
    were already started are stopped: the config says the server should be there, and a run that quietly goes
    on without its tools would look like a run that had them. Each server's stderr goes to
    `<stderr_dir>/<name>.log`, or nowhere without a `stderr_dir`.
    """
    with ExitStack() as stack:
        connections: dict[str, McpServerLike] = {}
        for name, server in config.servers.items():
            log = stderr_dir / f"{name}.log" if stderr_dir is not None else None
            connection = McpConnection(
                name, server, base_dir=config.base_dir, stderr_log=log, connect_timeout_s=connect_timeout_s
            )
            stack.enter_context(connection)  # starts it; if that fails, the stack closes the ones before it
            connections[name] = connection
        yield discover_tools(config, connections, max_result_chars=max_result_chars)
