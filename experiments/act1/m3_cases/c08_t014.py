import subprocess


class AgentHarness:
    def _run_command(self, command: str) -> str:
        """Run a shell command after checking it against the allowlist."""
        if not command.strip():
            return "Error: command is empty."

        base_cmd = command.strip().split()[0]
        if base_cmd not in self.allowed_commands:
            log.warning(f"Blocked: {command}")
            self._record(event="blocked_command", command=command)
            return f"Error: '{base_cmd}' is not on the command allowlist."

        try:
            result = subprocess.run(
                command, shell=True, cwd=self.workspace_dir,
                capture_output=True, text=True, timeout=30,
            )
            output = (result.stdout + result.stderr).strip() or "(no output)"
            status = "ok" if result.returncode == 0 else f"exit {result.returncode}"
            self._record(event="run_command", command=command, status=status)
            return output
        except subprocess.TimeoutExpired:
            self._record(event="run_command", command=command, status="timeout")
            return "Error: Command timed out after 30 seconds."
        except Exception as e:
            return f"Error: {e}"
