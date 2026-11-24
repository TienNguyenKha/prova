"""A small MCP server for the tests: a knowledge base of a few well-known weakness types.

Run by the tests as a child process. Logging goes through `logging`, which writes to stderr: stdout belongs
to the protocol. `--description-suffix TEXT` adds TEXT to the description of search_knowledge, to play a
server that changes what its tool says after it was approved.
"""

import argparse
import logging

from mcp.server import MCPServer

logger = logging.getLogger("knowledge")

ENTRIES = {
    "cwe-22": "Path traversal: a path built from user input reaches files outside the intended folder.",
    "cwe-78": "OS command injection: user input reaches a shell command and is run as part of it.",
    "cwe-79": "Cross-site scripting: user input is put in a page without being escaped for its context.",
    "cwe-89": "SQL injection: user input is put into a SQL statement instead of being passed as a parameter.",
    "cwe-918": "Server-side request forgery: the server fetches a URL the attacker chose, even internal.",
}


def build(description_suffix: str = "") -> MCPServer:
    server = MCPServer("knowledge")

    description = "Search the knowledge base for entries that mention the query." + description_suffix

    @server.tool(description=description)
    def search_knowledge(query: str, limit: int = 3) -> str:
        logger.info("search_knowledge query=%r limit=%d", query, limit)
        words = query.lower().split()
        hits = [f"{key}: {text}" for key, text in ENTRIES.items() if all(w in text.lower() for w in words)]
        return "\n".join(hits[:limit]) or "no entry matches"

    @server.tool()
    def get_entry(entry_id: str) -> str:
        """Return one entry by its id, for example cwe-89."""
        logger.info("get_entry id=%r", entry_id)
        if entry_id not in ENTRIES:
            raise ValueError(f"no entry called {entry_id}")
        return ENTRIES[entry_id]

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--description-suffix", default="")
    build(parser.parse_args().description_suffix).run()
