from command_gate import run_allowed


class Shell:
    """Stands in for the shell: remembers what it was asked to run."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def __call__(self, command: str) -> int:
        self.commands.append(command)
        return 0


def test_an_allowed_program_is_run() -> None:
    shell = Shell()
    outcome = run_allowed("ls -la src", shell)
    assert outcome.ran is True and shell.commands == ["ls -la src"]


def test_a_program_that_is_not_allowed_is_not_run() -> None:
    shell = Shell()
    outcome = run_allowed("rm -rf build", shell)
    assert outcome.ran is False and shell.commands == []


def test_a_second_command_after_a_semicolon_is_not_run() -> None:
    shell = Shell()
    outcome = run_allowed("ls src; rm -rf build", shell)
    assert outcome.ran is False and shell.commands == []
