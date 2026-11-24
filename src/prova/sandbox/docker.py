"""DockerSandbox: run a command in a throw-away container that can touch nothing but the workspace.

One container per command (`docker run --rm`), so nothing carries over from one command to the next. If
Docker is not there, `run` raises SandboxUnavailableError: it never falls back to running on the host.

Known limits: Docker Desktop shares the workspace through a virtual file system, so file ownership inside
the container is not the host's; and the isolation is only as strong as Docker's own (a kernel bug in the
VM is out of scope). Bytes beyond `max_output_bytes` are dropped, and the result says so.
"""

import codecs
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Self

from prova.errors import SandboxError, SandboxUnavailableError
from prova.sandbox.base import ExecResult

DEFAULT_IMAGE = "prova-sandbox:1"
CONTAINER_PREFIX = "prova-sbx-"
_READ_CHUNK = 65_536
_CHECK_TIMEOUT_S = 15.0
_REMOVAL_WAIT_S = 10.0


@dataclass
class _RunState:
    """What the two reader threads share while one command runs."""

    budget: int  # bytes of output we may still keep, for stdout and stderr together
    on_output: Callable[[str], None] | None
    truncated: bool = False
    abort: BaseException | None = None  # an exception the callback raised: re-raised once the run is over
    stdout: list[str] = field(default_factory=list[str])
    stderr: list[str] = field(default_factory=list[str])
    lock: threading.Lock = field(default_factory=threading.Lock)  # budget and the lists
    callback_lock: threading.Lock = field(default_factory=threading.Lock)  # one callback call at a time


class DockerSandbox:
    def __init__(
        self,
        workspace: Path,
        *,
        image: str = DEFAULT_IMAGE,
        cpus: float = 1,
        memory: str = "1g",
        pids_limit: int = 256,
        max_output_bytes: int = 1_000_000,
        docker_bin: str = "docker",
    ) -> None:
        resolved = workspace.resolve()
        if not resolved.is_dir():
            raise ValueError(f"the workspace does not exist or is not a directory: {workspace}")
        if "," in str(resolved):  # a comma would end the value of the --mount option early
            raise ValueError(f"the workspace path cannot contain a comma: {resolved}")
        self._workspace = resolved
        self._image = image
        self._cpus = cpus
        self._memory = memory
        self._pids_limit = pids_limit
        self._max_output_bytes = max_output_bytes
        self._docker = docker_bin
        self._available = False  # set once `docker version` has answered
        self._closed = False
        self._running: set[str] = set()
        self._running_lock = threading.Lock()

    # -- the command ---------------------------------------------------------------------------------

    def build_command(self, name: str, argv: list[str], cwd: str) -> list[str]:
        """The whole `docker run` command line. The arguments for the program inside come last, after the
        image, so Docker never reads them as its own options, and no shell is involved anywhere."""
        return [
            self._docker, "run",
            "--rm", "--name", name,
            "--network", "none",  # no network, not even a proxy
            "--cpus", f"{self._cpus:g}",
            "--memory", self._memory, "--memory-swap", self._memory,  # same value: no swap to hide behind
            "--pids-limit", str(self._pids_limit),
            "--user", "1000:1000",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",  # the image's own files cannot be changed...
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108 - ...a small scratch space, no exec
            "--mount", f"type=bind,src={self._workspace},dst=/work",  # the one thing shared with the host
            "-w", cwd,
            "--pull", "never",  # never fetch an image behind our back
            self._image,
            *argv,
        ]  # fmt: skip

    # -- running -------------------------------------------------------------------------------------

    def run(
        self,
        argv: list[str],
        *,
        timeout_s: float,
        cwd: str = "/work",
        on_output: Callable[[str], None] | None = None,
    ) -> ExecResult:
        """Run `argv` in a fresh container. `on_output` receives the text as it is printed; if it raises, the
        container is killed and the exception is raised from here."""
        if self._closed:
            raise SandboxError("the sandbox is closed")
        if not argv:
            raise ValueError("the command is empty")
        if timeout_s <= 0:
            raise ValueError("the timeout must be positive")
        if not (cwd == "/work" or (cwd.startswith("/work/") and ".." not in cwd.split("/"))):
            raise ValueError(f"the working directory must be /work or inside it, not {cwd!r}")

        self._check_docker()
        name = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
        state = _RunState(budget=self._max_output_bytes, on_output=on_output)
        with self._running_lock:
            self._running.add(name)
        started = time.perf_counter()
        try:
            proc = subprocess.Popen(  # noqa: S603 - a list we built; no shell; the model's words are only arguments
                self.build_command(name, argv, cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert proc.stdout is not None and proc.stderr is not None  # noqa: S101 - PIPE was asked for
            readers = [
                threading.Thread(target=self._pump, args=(stream, sink, state, name), daemon=True)
                for stream, sink in ((proc.stdout, state.stdout), (proc.stderr, state.stderr))
            ]
            for reader in readers:
                reader.start()
            timed_out = False
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill(name, proc)
            for reader in readers:
                reader.join(timeout=_REMOVAL_WAIT_S)
        finally:
            with self._running_lock:
                self._running.discard(name)
            self._wait_until_gone(name)

        if state.abort is not None:
            raise state.abort
        stdout, stderr = "".join(state.stdout), "".join(state.stderr)
        self._raise_if_docker_failed(proc.returncode, stderr)
        return ExecResult(
            exit_code=-1 if timed_out else proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_s=time.perf_counter() - started,
            truncated=state.truncated,
        )

    def _pump(self, stream: IO[bytes], sink: list[str], state: _RunState, name: str) -> None:
        """Read one stream until it ends, keep what fits in the budget, and pass it on as it arrives."""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = stream.fileno()
        while chunk := os.read(fd, _READ_CHUNK):
            with state.lock:
                take = min(len(chunk), state.budget)
                state.budget -= take
                if take < len(chunk):
                    state.truncated = True
            if take == 0:
                continue  # keep reading so the program is never blocked on a full pipe, but keep nothing
            text = decoder.decode(chunk[:take])
            if not text:
                continue
            with state.lock:
                sink.append(text)
            self._deliver(text, state, name)
        if not state.truncated:
            tail = decoder.decode(b"", final=True)
            if tail:
                with state.lock:
                    sink.append(tail)

    def _deliver(self, text: str, state: _RunState, name: str) -> None:
        if state.on_output is None or state.abort is not None:
            return
        with state.callback_lock:
            try:
                state.on_output(text)
            except BaseException as e:  # noqa: BLE001 - whatever the caller raised is handed back, after the kill
                state.abort = e
                self._kill(name, None)

    # -- Docker itself -------------------------------------------------------------------------------

    def _check_docker(self) -> None:
        if self._available:
            return
        try:
            done = subprocess.run(  # noqa: S603 - fixed arguments
                [self._docker, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=_CHECK_TIMEOUT_S,
                check=False,
            )
        except FileNotFoundError:
            raise SandboxUnavailableError(f"Docker is not installed (no program {self._docker!r})") from None
        except subprocess.TimeoutExpired:
            raise SandboxUnavailableError("Docker did not answer in time") from None
        if done.returncode != 0:
            raise SandboxUnavailableError("Docker is not running: start it and try again")
        self._available = True

    def _raise_if_docker_failed(self, returncode: int, stderr: str) -> None:
        """Docker's own failures, as opposed to the command's. Exit code 125 is Docker's too."""
        if "Unable to find image" in stderr:
            build = f"docker build -t {self._image} docker/sandbox"
            message = f"the image {self._image!r} is not on this machine: build it with `{build}`"
            raise SandboxUnavailableError(message)
        if "Cannot connect to the Docker daemon" in stderr:
            self._available = False
            raise SandboxUnavailableError("Docker is not running: start it and try again")
        if returncode == 125 and stderr.startswith("docker:"):
            raise SandboxError(f"Docker could not start the container: {stderr.strip()[:300]}")

    def _kill(self, name: str, proc: "subprocess.Popen[bytes] | None") -> None:
        """Stop the container. It may not exist yet if the command only just started, so try a few times."""
        for _ in range(20):
            done = subprocess.run(  # noqa: S603 - fixed arguments
                [self._docker, "kill", name], capture_output=True, check=False, timeout=30
            )
            if done.returncode == 0 or (proc is not None and proc.poll() is not None):
                break
            time.sleep(0.25)
        if proc is not None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _wait_until_gone(self, name: str) -> None:
        """`--rm` removes the container a moment after it stops; do not return before it is gone."""
        deadline = time.monotonic() + _REMOVAL_WAIT_S
        while time.monotonic() < deadline:
            try:
                done = subprocess.run(  # noqa: S603 - fixed arguments
                    [self._docker, "ps", "-aq", "--filter", f"name=^{name}$"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return
            if done.returncode != 0 or not done.stdout.strip():
                return
            time.sleep(0.1)

    # -- closing -------------------------------------------------------------------------------------

    def close(self) -> None:
        """Kill any command still running and refuse new ones. Safe to call more than once."""
        self._closed = True
        with self._running_lock:
            names = list(self._running)
        for name in names:
            self._kill(name, None)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
