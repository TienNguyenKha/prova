"""An MCP server for the tests that behaves badly on request: an error, a dying process, a hanging call.

`--noisy` prints a line to stdout before serving, which is the mistake that breaks a stdio server's channel.
"""

import argparse
import logging
import os
import time
from pathlib import Path

from mcp.server import MCPServer

logger = logging.getLogger("behaviours")

server = MCPServer("behaviours")


@server.tool()
def echo(text: str) -> str:
    """Repeat the text."""
    logger.info("echo %r", text)
    return text


@server.tool()
def boom() -> str:
    """Raise an exception inside the tool."""
    raise RuntimeError("kaboom")


@server.tool()
def die() -> str:
    """Kill the whole server process in the middle of a call."""
    os._exit(3)


@server.tool()
def hang() -> str:
    """Never answer."""
    time.sleep(3600)
    return "too late"


@server.tool()
def pid() -> str:
    """The process id of this server."""
    return str(os.getpid())


@server.tool()
def where() -> str:
    """The working directory of this server."""
    return str(Path.cwd())


@server.tool()
def env_names() -> str:
    """The names of the environment variables this server can see."""
    return "\n".join(sorted(os.environ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--noisy", action="store_true")
    parser.add_argument("--pidfile", help="write the process id here as soon as the server starts")
    args = parser.parse_args()
    if args.pidfile:
        Path(args.pidfile).write_text(str(os.getpid()), encoding="utf-8")
    if args.noisy:
        print("Loading the knowledge base...")  # noqa: T201 - this is the mistake the test plays
    server.run()
