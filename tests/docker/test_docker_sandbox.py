"""Tests that run real containers. They need Docker and the image:
  docker build -t prova-sandbox:1 docker/sandbox

Run them with:  uv run pytest -m docker -q
"""

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from prova.sandbox.docker import DockerSandbox

pytestmark = pytest.mark.docker


def leftover_containers() -> list[str]:
    done = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=prova-sbx-"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.split()


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


@pytest.fixture(autouse=True)
def no_container_is_left_behind() -> Iterator[None]:
    yield
    assert leftover_containers() == []  # every test, whatever it did: orphans pile up otherwise


def py(code: str) -> list[str]:
    return ["python", "-c", code]


# --- The plan's table ---------------------------------------------------------


def test_a_command_runs_and_its_output_and_exit_code_come_back(work: Path) -> None:
    with DockerSandbox(work) as sandbox:
        ok = sandbox.run(["echo", "hi"], timeout_s=30)
        failed = sandbox.run(["sh", "-c", "echo oops >&2; exit 3"], timeout_s=30)
    assert (ok.exit_code, ok.stdout, ok.timed_out, ok.truncated) == (0, "hi\n", False, False)
    assert failed.exit_code == 3 and failed.stderr == "oops\n"


def test_an_endless_loop_is_cut_at_the_timeout_and_the_container_is_removed(work: Path) -> None:
    started = time.monotonic()
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(py("while True: pass"), timeout_s=2)
    assert result.timed_out
    assert time.monotonic() - started < 20  # stopped at about the timeout, not left to run


def test_a_small_fork_bomb_is_stopped_by_the_process_limit(work: Path) -> None:
    code = (
        "import os, time\n"
        "pids = []\n"
        "try:\n"
        "    for _ in range(500):\n"
        "        pid = os.fork()\n"
        "        if pid == 0:\n"
        "            time.sleep(20); os._exit(0)\n"
        "        pids.append(pid)\n"
        "    print('NOT BLOCKED', len(pids))\n"
        "except OSError:\n"
        "    print('blocked after', len(pids))\n"
        "finally:\n"
        "    for pid in pids: os.kill(pid, 9)\n"
    )
    with DockerSandbox(work, pids_limit=32) as sandbox:
        result = sandbox.run(py(code), timeout_s=60)
    assert "blocked after" in result.stdout, result.output
    assert not result.timed_out


def test_there_is_no_network(work: Path) -> None:
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('CONNECTED')\n"
        "except OSError as e:\n"
        "    print('no network:', type(e).__name__)\n"
    )
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(py(code), timeout_s=30)
    assert "no network" in result.stdout and "CONNECTED" not in result.stdout


@pytest.mark.parametrize("target", ["/etc/passwd", "/usr/pwned", "/pwned"])
def test_nothing_outside_work_and_tmp_can_be_written(work: Path, target: str) -> None:
    code = f"open({target!r}, 'a').write('x')"
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(py(code), timeout_s=30)
    assert result.exit_code != 0
    assert "Error" in result.stderr  # a PermissionError or a read-only file system


def test_a_file_written_in_work_is_there_on_the_host_afterwards(work: Path) -> None:
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(py("open('made.txt', 'w').write('from the sandbox')"), timeout_s=30)
    assert result.exit_code == 0, result.output
    assert (work / "made.txt").read_text(encoding="utf-8") == "from the sandbox"


def test_tmp_is_writable_but_nothing_in_it_can_be_executed(work: Path) -> None:
    code = (
        "import os, subprocess\n"
        "open('/tmp/run.sh', 'w').write('#!/bin/sh\\necho ran')\n"
        "os.chmod('/tmp/run.sh', 0o755)\n"
        "try:\n"
        "    subprocess.run(['/tmp/run.sh'], check=True)\n"
        "    print('EXECUTED')\n"
        "except OSError as e:\n"
        "    print('not executable:', type(e).__name__)\n"
    )
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(py(code), timeout_s=30)
    assert "not executable" in result.stdout, result.output


def test_a_shell_operator_is_just_an_argument_and_no_second_command_runs(work: Path) -> None:
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(["echo", "a", "&&", "touch", "/work/pwned"], timeout_s=30)
    assert result.stdout == "a && touch /work/pwned\n"
    assert not (work / "pwned").exists()


# --- More isolation -----------------------------------------------------------


def test_the_command_does_not_run_as_root(work: Path) -> None:
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(["id", "-u"], timeout_s=30)
    assert result.stdout.strip() == "1000"


def test_the_environment_of_the_host_does_not_reach_the_container(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-this-must-not-reach-the-container")
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(["env"], timeout_s=30)
    assert "ANTHROPIC" not in result.stdout and "sk-ant" not in result.stdout


def test_only_the_workspace_of_the_host_is_visible(work: Path, tmp_path: Path) -> None:
    (tmp_path / "private.txt").write_text("host secret", encoding="utf-8")
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(["sh", "-c", f"ls / ; cat {tmp_path}/private.txt"], timeout_s=30)
    assert "host secret" not in result.output
    assert result.exit_code != 0


def test_the_working_directory_can_be_a_subdirectory_of_work(work: Path) -> None:
    (work / "sub").mkdir()
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(["pwd"], timeout_s=30, cwd="/work/sub")
    assert result.stdout.strip() == "/work/sub"


# --- Output: streamed, capped, abortable --------------------------------------


def test_output_is_streamed_to_the_callback_as_it_is_printed(work: Path) -> None:
    seen: list[str] = []
    with DockerSandbox(work) as sandbox:
        result = sandbox.run(["sh", "-c", "echo one; echo two"], timeout_s=30, on_output=seen.append)
    assert "".join(seen).splitlines() == ["one", "two"]
    assert result.stdout == "one\ntwo\n"


def test_output_over_the_limit_is_cut_and_says_so(work: Path) -> None:
    with DockerSandbox(work, max_output_bytes=1_000) as sandbox:
        result = sandbox.run(py("print('x' * 100_000)"), timeout_s=30)
    assert result.truncated
    assert len(result.stdout.encode()) <= 1_000
    assert result.exit_code == 0  # the command still finished; only what we keep was cut


def test_the_callback_can_abort_the_run_and_the_container_is_killed(work: Path) -> None:
    class Stop(Exception):  # noqa: N818 - a test signal, not an error
        pass

    def on_output(text: str) -> None:
        if "first" in text:
            raise Stop

    started = time.monotonic()
    with DockerSandbox(work) as sandbox, pytest.raises(Stop):
        sandbox.run(["sh", "-c", "echo first; sleep 60; echo never"], timeout_s=120, on_output=on_output)
    assert time.monotonic() - started < 30  # not 60 seconds


# --- Cleaning up --------------------------------------------------------------


def test_the_sandbox_works_again_for_the_next_command_after_a_timeout(work: Path) -> None:
    with DockerSandbox(work) as sandbox:
        assert sandbox.run(py("while True: pass"), timeout_s=2).timed_out
        assert sandbox.run(["echo", "still fine"], timeout_s=30).stdout == "still fine\n"
