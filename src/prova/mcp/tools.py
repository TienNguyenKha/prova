"""What a server's tool list and answers become before the model sees them.

Everything an MCP server says is data from somebody else's program. These functions decide what of it is
let through. A tool that does not pass is left out, never changed: cutting a description would edit the text
the model reads, and that is the server's text, not ours to rewrite.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence

import mcp.types as types

from prova.errors import ErrorCode
from prova.tools.base import ToolResult

MAX_FULL_NAME_CHARS = 64  # the API's limit on the whole tool name
MAX_DESCRIPTION_CHARS = 2_000
MAX_SCHEMA_CHARS = 20_000  # the schema as JSON
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]+")


def namespaced_name(server: str, tool: str) -> str:
    """The name the model sees. The server's name in front: two servers cannot shadow each other."""
    return f"mcp__{server}__{tool}"


def check_tool(server: str, tool: types.Tool) -> str | None:
    """Why this tool may not be offered to the model, or None if it may. The reason names the rule that was
    broken and never repeats the server's text: that text is not trusted, and the reason goes to the log."""
    if not _TOOL_NAME.fullmatch(tool.name):
        return "the tool name has characters the API does not allow (only letters, digits, _ and -)"
    full = namespaced_name(server, tool.name)
    if len(full) > MAX_FULL_NAME_CHARS:
        return f"the full tool name would be {len(full)} characters; the limit is {MAX_FULL_NAME_CHARS}"
    description = tool.description or ""
    if len(description) > MAX_DESCRIPTION_CHARS:
        return f"the description is {len(description)} characters; the limit is {MAX_DESCRIPTION_CHARS}"
    return _check_schema(tool.input_schema)


def _check_schema(schema: dict[str, object]) -> str | None:
    if schema.get("type") != "object":
        return "the input schema is not an object schema (its type must be 'object')"
    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        return "the input schema is malformed: 'properties' must be a mapping"
    required: object = schema.get("required")
    if required is not None and not _is_list_of_names(required):
        return "the input schema is malformed: 'required' must be a list of names"
    size = len(json.dumps(schema))
    if size > MAX_SCHEMA_CHARS:
        return f"the input schema is too large ({size} characters; the limit is {MAX_SCHEMA_CHARS})"
    return None


def hash_tools(tools: Sequence[types.Tool]) -> str:
    """A fingerprint of what the model would see of these tools: name, description and input schema. The
    order of the tools and of the keys does not matter. A server that changes its mind changes the hash."""
    shown = sorted(
        ({"name": t.name, "description": t.description or "", "input_schema": t.input_schema} for t in tools),
        key=lambda entry: str(entry["name"]),
    )
    canonical = json.dumps(shown, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def result_to_tool_result(result: types.CallToolResult, *, max_chars: int) -> ToolResult:
    """What the model is shown of a server's answer: the text, cut if it is long, and a line for anything that
    is not text and was left out. An error reported by the server is a `remote_error`."""
    texts: list[str] = []
    omitted: Counter[str] = Counter()
    for block in result.content:
        if isinstance(block, types.TextContent):
            texts.append(block.text)
        else:
            omitted[block.type] += 1
    body = "\n".join(texts)
    if not body and result.structured_content is not None:
        body = json.dumps(result.structured_content, sort_keys=True, ensure_ascii=False)
    notes = [f"[{n} {kind} {_plural(n)} omitted: not text]" for kind, n in omitted.items()]
    shown = "\n".join(part for part in (_cut(body, max_chars), *notes) if part)

    if result.is_error:
        silent = "the tool reported an error without saying what"
        return ToolResult.error(ErrorCode.REMOTE_ERROR, shown or silent)
    if not shown:
        return ToolResult.ok("the tool returned nothing")
    return ToolResult.ok("the tool answered", snapshot=shown)


def _is_list_of_names(value: object) -> bool:
    if not isinstance(value, list):
        return False
    items: list[object] = list(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return all(isinstance(item, str) for item in items)


def _plural(n: int) -> str:
    return "block" if n == 1 else "blocks"


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[cut: showing the first {limit} of {len(text)} characters]"
