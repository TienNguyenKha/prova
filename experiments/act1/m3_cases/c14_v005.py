def run_data_analyst(task: str, data_path: str | None = None, max_iterations: int = 5):
    """Run the data analyst agent with (optionally) E2B sandbox."""
    use_e2b = False
    sandbox = None
    try:
        from e2b_code_interpreter import Sandbox
        sandbox = Sandbox()
        use_e2b = True
    except ImportError:
        print("E2B not installed, using local subprocess")
    except Exception as e:
        print(f"E2B unavailable ({e}), using local subprocess")

    messages = [{"role": "user", "content": task}]

    for iteration in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            system=CODE_AGENT_PROMPT,
            messages=messages,
            max_tokens=2048,
        )

        content = response.content[0].text
        messages.append({"role": "assistant", "content": content})
        code = extract_code_block(content)

        if not code:
            break

        if use_e2b and sandbox:
            result = sandbox.run_code(code)
            output = result.text or ""
            error = str(result.error) if result.error else ""
        else:
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                f.flush()
                try:
                    proc = subprocess.run(
                        ["python", f.name], capture_output=True, text=True, timeout=30
                    )
                    output = proc.stdout
                    error = proc.stderr
                except subprocess.TimeoutExpired:
                    output = ""
                    error = "Timeout: code took more than 30 seconds."
