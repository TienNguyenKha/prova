import anthropic

client = anthropic.Anthropic()


def mock_agent(message: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        system="You are Atlas, a research and code review assistant.",
        messages=[{"role": "user", "content": message}],
        max_tokens=512,
    )
    return {
        "answer": response.content[0].text,
        "tools_used": ["web_search"],
        "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
    }


def score_tool_use(output: dict, expected_tools: list[str]) -> float:
    used_tools = output.get("tools_used", [])
    hits = sum(1 for tool in expected_tools if tool in used_tools)
    return hits / max(len(expected_tools), 1)
