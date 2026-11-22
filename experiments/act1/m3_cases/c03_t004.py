import json
from pathlib import Path

from mcp.server.stdio import stdio_server


async def main():
    init_db()
    print(f"Atlas Knowledge MCP Server starting...")
    print(f"Database: {DB_PATH}")
    print(f"Transport: stdio\n")

    card_path = Path(__file__).parent / ".well-known" / "agent.json"
    card_path.parent.mkdir(exist_ok=True)
    card_path.write_text(json.dumps(AGENT_CARD, indent=2))
    print(f"Agent Card saved to {card_path}\n")

    async with stdio_server() as (read, write):
        await server.run(read, write)
