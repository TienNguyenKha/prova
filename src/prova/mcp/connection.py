"""McpConnection: one live connection to one MCP server, used from ordinary (synchronous) code.

The MCP SDK is asyncio. The rest of Prova is not, and should not have to be. So each connection owns a
thread that runs an event loop for as long as the connection lives, and the methods here hand work to that
loop and wait for the answer, with a time limit. Nothing about asyncio leaks out except the SDK's own
result types.

The server is a program somebody else wrote and it runs on this machine, as a child process, with the minimal
environment the SDK gives it (the API key is not in it). Only servers listed in mcp.yaml are ever started.
"""

import asyncio
import concurrent.futures
import logging
import os
import threading
from contextlib import AsyncExitStack
from enum import Enum, auto
from pathlib import Path
from typing import Any, Self, TextIO

import mcp.types as types
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

from prova.errors import McpConnectionError, McpServerDiedError, McpStartError, McpTimeoutError
from prova.mcp.config import McpServerConfig
from prova.mcp.tools import hash_tools

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_S = 15.0
_JOIN_TIMEOUT_S = 15.0  # how long close() waits for the thread and the server process to go
_CALL_GRACE_S = 5.0  # on top of a call's timeout, for the SDK to report its own timeout first


class _State(Enum):
    NEW = auto()
    READY = auto()
    DEAD = auto()  # the server went away on its own
    CLOSED = auto()


class McpConnection:
    def __init__(
        self,
        name: str,
        config: McpServerConfig,
        *,
        base_dir: Path,
        stderr_log: Path | None = None,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    ) -> None:
        self._name = name
        self._config = config
        self._base_dir = base_dir
        self._stderr_log = stderr_log
        self._connect_timeout_s = connect_timeout_s
        self._state = _State.NEW
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()  # set when the handshake is over, well or badly
        self._stop: asyncio.Event | None = None  # set from outside to end the connection; lives on the loop
        self._client: Client | None = None
        self._tools: tuple[types.Tool, ...] = ()
        self._tools_hash = ""
        self._start_error: McpStartError | None = None

    # -- what the server offers ----------------------------------------------------------------------

    @property
    def tools(self) -> tuple[types.Tool, ...]:
        """Every tool the server advertised when we connected, unchecked: see prova.mcp.tools.check_tool."""
        self._require_started()
        return self._tools

    @property
    def tools_hash(self) -> str:
        """A fingerprint of those tools, to notice a server that changes what it says later."""
        self._require_started()
        return self._tools_hash

    @property
    def alive(self) -> bool:
        return self._state is _State.READY

    # -- starting and stopping -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the server and finish the handshake. On McpStartError the connection is closed for good."""
        with self._lock:
            if self._state is _State.CLOSED:
                raise McpConnectionError(f"the connection to the MCP server {self._name!r} is closed")
            if self._thread is not None:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run, name=f"mcp-{self._name}", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout=self._connect_timeout_s + _CALL_GRACE_S):
            self.close()
            seconds = f"{self._connect_timeout_s:g}"
            raise McpStartError(f"the MCP server {self._name!r} did not answer within {seconds} seconds")
        if self._start_error is not None:
            error = self._start_error
            self.close()
            raise error
        self._state = _State.READY

    def close(self) -> None:
        """End the server process and the thread. Safe to call more than once, and after a failed start."""
        with self._lock:
            if self._state is _State.CLOSED:
                return
            self._state = _State.CLOSED
            loop, thread, stop = self._loop, self._thread, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)
        if thread is not None:
            thread.join(timeout=_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.warning("the thread of the MCP connection %r did not stop in time", self._name)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- calling a tool ------------------------------------------------------------------------------

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout_s: float | None = None
    ) -> types.CallToolResult:
        """Call a tool of the server and wait. An error reported by the tool is in the result (`is_error`); an
        exception means the call itself failed: it timed out, or the server is gone."""
        if self._state is _State.CLOSED:
            raise McpConnectionError(f"the connection to the MCP server {self._name!r} is closed")
        if self._state is _State.DEAD:
            raise McpServerDiedError(f"the MCP server {self._name!r} is not running any more")
        self._require_started()
        timeout = timeout_s if timeout_s is not None else self._config.timeout_s
        assert self._loop is not None  # noqa: S101 - READY means the loop exists
        future = asyncio.run_coroutine_threadsafe(self._call(name, arguments, timeout), self._loop)
        try:
            return future.result(timeout=timeout + _CALL_GRACE_S)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise McpTimeoutError(self._timeout_message(name, timeout)) from None

    async def _call(self, name: str, arguments: dict[str, Any], timeout: float) -> types.CallToolResult:
        assert self._client is not None  # noqa: S101 - set before the connection became READY
        try:
            return await self._client.call_tool(name, arguments, read_timeout_seconds=timeout)
        except MCPError as e:
            if e.code == types.REQUEST_TIMEOUT:
                raise McpTimeoutError(self._timeout_message(name, timeout)) from None
            if e.code == types.CONNECTION_CLOSED:
                self._state = _State.DEAD
                message = f"the MCP server {self._name!r} stopped while the tool {name!r} was running"
                raise McpServerDiedError(message) from None
            message = f"the MCP server {self._name!r} refused the call (code {e.code})"
            raise McpConnectionError(message) from None

    def _timeout_message(self, tool: str, timeout: float) -> str:
        return f"the tool {tool!r} of the MCP server {self._name!r} did not answer within {timeout:g} seconds"

    def _require_started(self) -> None:
        if self._state is _State.NEW:
            raise McpConnectionError(f"the connection to the MCP server {self._name!r} is not started")

    # -- the thread ----------------------------------------------------------------------------------

    def _run(self) -> None:
        loop = self._loop
        assert loop is not None  # noqa: S101 - start() made it before starting this thread
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception:
            logger.exception("the MCP connection %r ended with an unexpected error", self._name)
        finally:
            if self._state is _State.READY:
                self._state = _State.DEAD  # the loop ended while nobody asked it to
            self._ready.set()  # a start that is still waiting must not wait for ever
            loop.close()

    async def _main(self) -> None:
        """Lives as long as the connection: starts the server, then waits to be told to stop. The server is
        entered and left in this one task, as the SDK's context managers require."""
        self._stop = asyncio.Event()
        errlog: TextIO | None = None
        try:
            errlog = self._open_errlog()
            params = StdioServerParameters(
                command=self._config.command[0], args=list(self._config.command[1:]), cwd=self._base_dir
            )
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(self._connect_timeout_s):
                        client = await stack.enter_async_context(Client(stdio_client(params, errlog=errlog)))
                        listed = await client.list_tools()
                except TimeoutError:
                    seconds = f"{self._connect_timeout_s:g}"
                    self._start_error = McpStartError(
                        f"the MCP server {self._name!r} did not answer within {seconds} seconds"
                    )
                    return
                except (OSError, MCPError, ExceptionGroup) as e:
                    reason = f"{type(e).__name__}: {str(e)[:200]}"
                    message = f"the MCP server {self._name!r} could not be started: {reason}"
                    self._start_error = McpStartError(message)
                    return
                self._client = client
                self._tools = tuple(listed.tools)
                self._tools_hash = hash_tools(self._tools)
                self._ready.set()
                await self._stop.wait()
        finally:
            if errlog is not None:
                errlog.close()
            self._ready.set()

    def _open_errlog(self) -> TextIO:
        """Where the server's stderr goes: a log file if there is one, else nowhere. Never the terminal."""
        if self._stderr_log is None:
            return Path(os.devnull).open("w", encoding="utf-8")  # noqa: SIM115 - closed in _main
        self._stderr_log.parent.mkdir(parents=True, exist_ok=True)
        return self._stderr_log.open("a", encoding="utf-8")
