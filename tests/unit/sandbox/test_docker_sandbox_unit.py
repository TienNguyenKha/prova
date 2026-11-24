"""Tests for prova.sandbox.docker.DockerSandbox that need no Docker: the command it builds, and what it does
when Docker is missing, stopped or has no image. The tests that run containers are in tests/docker."""

import stat
from pathlib import Path

import pytest

from prova.errors import SandboxError, SandboxUnavailableError
from prova.sandbox.docker import DockerSandbox


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def command(sandbox: DockerSandbox, argv: list[str] | None = None, cwd: str = "/work") -> list[str]:
    return sandbox.build_command("prova-sbx-test", argv or ["python", "-m", "pytest"], cwd)


def has_pair(cmd: list[str], first: str, second: str) -> bool:
    return any(cmd[i] == first and cmd[i + 1] == second for i in range(len(cmd) - 1))


def fake_docker(tmp_path: Path, *, version_exit: int = 0, run_exit: int = 0, run_stderr: str = "") -> str:
    """A stand-in `docker` program. `docker version` answers with version_exit, anything else with run_exit.
    Every call is appended to calls.log next to it."""
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir(exist_ok=True)
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{script.parent}/calls.log"\n'
        'if [ "$1" = "version" ]; then\n'
        f'  if [ {version_exit} -ne 0 ]; then echo "Cannot connect to the Docker daemon" >&2; fi\n'
        f"  exit {version_exit}\n"
        "fi\n"
        f'echo "{run_stderr}" >&2\n'
        f"exit {run_exit}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def calls(docker_bin: str) -> list[str]:
    log = Path(docker_bin).parent / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


# --- The command: every security flag is a promise ----------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("--network", "none"),
        ("--cpus", "1"),
        ("--memory", "1g"),
        ("--memory-swap", "1g"),
        ("--pids-limit", "256"),
        ("--user", "1000:1000"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--pull", "never"),
    ],
)
def test_the_command_has_every_isolation_flag(work: Path, first: str, second: str) -> None:
    assert has_pair(command(DockerSandbox(work)), first, second)


def test_the_container_is_removed_and_its_root_file_system_is_read_only(work: Path) -> None:
    cmd = command(DockerSandbox(work))
    assert "--rm" in cmd and "--read-only" in cmd
    assert any(part.startswith("/tmp:") and "noexec" in part for part in cmd)  # noqa: S108 - scratch only


def test_the_limits_can_be_changed_when_the_sandbox_is_made(work: Path) -> None:
    cmd = command(DockerSandbox(work, cpus=2, memory="512m", pids_limit=32))
    assert has_pair(cmd, "--cpus", "2") and has_pair(cmd, "--memory", "512m")
    assert has_pair(cmd, "--pids-limit", "32")


def test_nothing_that_weakens_the_isolation_is_ever_passed(work: Path) -> None:
    cmd = command(DockerSandbox(work))
    for forbidden in ("--privileged", "-e", "--env", "--env-file", "--cap-add", "--device", "-v", "--volume"):
        assert forbidden not in cmd
    assert not has_pair(cmd, "--network", "host")


def test_the_workspace_is_the_only_mount_and_goes_to_work(work: Path) -> None:
    cmd = command(DockerSandbox(work))
    mounts = [cmd[i + 1] for i in range(len(cmd) - 1) if cmd[i] == "--mount"]
    assert len(mounts) == 1
    assert "type=bind" in mounts[0] and "dst=/work" in mounts[0] and f"src={work.resolve()}" in mounts[0]


def test_the_working_directory_is_passed_to_the_container(work: Path) -> None:
    assert has_pair(command(DockerSandbox(work), cwd="/work/sub"), "-w", "/work/sub")


def test_the_arguments_come_last_after_the_image_and_stay_as_they_are(work: Path) -> None:
    sandbox = DockerSandbox(work, image="prova-sandbox:1")
    argv = ["pytest", "&&", "touch", "/tmp/x", "--privileged", "$(whoami)"]  # noqa: S108
    cmd = command(sandbox, argv)
    assert cmd[cmd.index("prova-sandbox:1") + 1 :] == argv  # no shell: these are plain words for the program


def test_every_container_gets_a_name_the_sandbox_can_kill_it_by(work: Path) -> None:
    cmd = command(DockerSandbox(work))
    assert has_pair(cmd, "--name", "prova-sbx-test")


# --- Misuse is loud -----------------------------------------------------------


def test_a_workspace_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        DockerSandbox(tmp_path / "nope")


def test_a_workspace_path_with_a_comma_is_refused_it_would_break_the_mount(tmp_path: Path) -> None:
    bad = tmp_path / "a,b"
    bad.mkdir()
    with pytest.raises(ValueError, match="comma"):
        DockerSandbox(bad)


def test_an_empty_command_is_refused(work: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        DockerSandbox(work).run([], timeout_s=5)


@pytest.mark.parametrize("cwd", ["/etc", "/workfoo", "/work/../etc", "work", ""])
def test_a_working_directory_outside_work_is_refused(work: Path, cwd: str) -> None:
    with pytest.raises(ValueError, match="/work"):
        DockerSandbox(work).run(["ls"], timeout_s=5, cwd=cwd)


@pytest.mark.parametrize("timeout_s", [0, -1])
def test_a_timeout_that_is_not_positive_is_refused(work: Path, timeout_s: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        DockerSandbox(work).run(["ls"], timeout_s=timeout_s)


# --- Never on the host --------------------------------------------------------


def test_a_missing_docker_program_is_unavailable_and_nothing_else_is_tried(work: Path) -> None:
    sandbox = DockerSandbox(work, docker_bin="/no/such/docker")
    with pytest.raises(SandboxUnavailableError, match="not installed"):
        sandbox.run(["echo", "hi"], timeout_s=5)


def test_a_stopped_docker_is_unavailable_and_no_container_is_started(tmp_path: Path, work: Path) -> None:
    docker_bin = fake_docker(tmp_path, version_exit=1)
    with pytest.raises(SandboxUnavailableError, match="not running"):
        DockerSandbox(work, docker_bin=docker_bin).run(["echo", "hi"], timeout_s=5)
    assert all(call.startswith("version") for call in calls(docker_bin))  # only the check ran


def test_a_missing_image_says_how_to_build_it(tmp_path: Path, work: Path) -> None:
    missing = "Unable to find image 'prova-sandbox:1' locally"
    docker_bin = fake_docker(tmp_path, run_exit=125, run_stderr=missing)
    with pytest.raises(SandboxUnavailableError, match="docker build"):
        DockerSandbox(work, docker_bin=docker_bin).run(["echo", "hi"], timeout_s=5)


def test_a_run_after_close_is_refused(work: Path) -> None:
    sandbox = DockerSandbox(work)
    sandbox.close()
    with pytest.raises(SandboxError, match="closed"):
        sandbox.run(["echo", "hi"], timeout_s=5)


def test_close_can_be_called_twice(work: Path) -> None:
    sandbox = DockerSandbox(work)
    sandbox.close()
    sandbox.close()


def test_it_works_as_a_context_manager_and_is_closed_afterwards(work: Path) -> None:
    with DockerSandbox(work) as sandbox:
        assert isinstance(sandbox, DockerSandbox)
    with pytest.raises(SandboxError, match="closed"):
        sandbox.run(["echo", "hi"], timeout_s=5)
