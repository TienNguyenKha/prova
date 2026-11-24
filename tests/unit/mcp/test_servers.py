"""Tests for prova.mcp.servers.open_mcp_servers: start every configured server, hand over its tools, and make
sure none of them is left running, whatever happens in between."""

import os
import sys
import time
from pathlib import Path
from types import MappingProxyType

import pytest

from prova.errors import McpStartError
from prova.harness.permissions import Permission
from prova.mcp.config import McpConfig, McpServerConfig
from prova.mcp.servers import open_mcp_servers
from prova.tools.base import RunContext

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "mcp"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone_soon(pid: int, seconds: float = 8) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


def config(**servers: McpServerConfig) -> McpConfig:
    return McpConfig(servers=MappingProxyType(servers), base_dir=FIXTURES)


def python_server(script: str, *args: str, permission: Permission = Permission.READ) -> McpServerConfig:
    return McpServerConfig(command=(sys.executable, script, *args), permission=permission, timeout_s=30)


def pid_of(found: object, server: str) -> int:
    adapters = getattr(found, "adapters")  # noqa: B009
    adapter = next(a for a in adapters if a.name == f"mcp__{server}__pid")
    result = adapter.run(adapter.args_model(), RunContext(workspace=Path.cwd()))
    return int(result.snapshot)


def test_every_configured_server_is_started_and_its_tools_are_handed_over(tmp_path: Path) -> None:
    both = config(
        knowledge=python_server("knowledge_server.py"), behaviours=python_server("behaviours_server.py")
    )
    with open_mcp_servers(both, stderr_dir=tmp_path) as found:
        names = {a.name for a in found.adapters}
        assert "mcp__knowledge__search_knowledge" in names and "mcp__behaviours__echo" in names
        assert set(found.hashes) == {"knowledge", "behaviours"}


def test_the_servers_are_stopped_when_the_block_ends(tmp_path: Path) -> None:
    with open_mcp_servers(
        config(behaviours=python_server("behaviours_server.py")), stderr_dir=tmp_path
    ) as found:
        pid = pid_of(found, "behaviours")
        assert alive(pid)
    assert gone_soon(pid)


def test_the_servers_are_stopped_when_something_inside_the_block_fails(tmp_path: Path) -> None:
    pids: list[int] = []
    with (
        pytest.raises(RuntimeError),
        open_mcp_servers(
            config(behaviours=python_server("behaviours_server.py")), stderr_dir=tmp_path
        ) as found,
    ):
        pids.append(pid_of(found, "behaviours"))
        raise RuntimeError("the run went wrong")
    assert gone_soon(pids[0])


def test_each_server_logs_to_its_own_file(tmp_path: Path) -> None:
    with open_mcp_servers(config(knowledge=python_server("knowledge_server.py")), stderr_dir=tmp_path):
        pass
    assert (tmp_path / "knowledge.log").exists()


def test_a_server_that_cannot_start_stops_the_whole_run_and_the_ones_already_started_are_closed(
    tmp_path: Path,
) -> None:
    pidfile = tmp_path / "first.pid"
    broken = McpServerConfig(command=("/no/such/program",), permission=Permission.READ)
    both = config(first=python_server("behaviours_server.py", "--pidfile", str(pidfile)), second=broken)

    with pytest.raises(McpStartError, match="second"), open_mcp_servers(both, stderr_dir=tmp_path):
        pytest.fail("the block must not run when a server did not start")

    assert gone_soon(int(pidfile.read_text(encoding="utf-8")))  # the first server was started, then closed


def test_a_server_that_never_answers_fails_after_the_connect_timeout(tmp_path: Path) -> None:
    silent = McpServerConfig(
        command=(sys.executable, "-c", "import time; time.sleep(60)"), permission=Permission.READ
    )
    started = time.monotonic()
    with (
        pytest.raises(McpStartError, match="did not answer"),
        open_mcp_servers(config(silent=silent), stderr_dir=tmp_path, connect_timeout_s=2),
    ):
        pytest.fail("the block must not run")
    assert time.monotonic() - started < 15


def test_with_no_servers_configured_nothing_is_started(tmp_path: Path) -> None:
    with open_mcp_servers(config(), stderr_dir=tmp_path) as found:
        assert found.adapters == () and found.skipped == () and found.hashes == {}
