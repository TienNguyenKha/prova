"""Build the prompt: what the model is told as fact (the system prompt), and the task somebody else wrote.

The system prompt is made of static layers, in a fixed order, and it only talks about the tools this run is
really given: a read-only run is never told the name of a tool that writes. The task is data from somebody
else, so it goes in the user turn inside a tag, and the tag cannot be closed from inside.

Everything is built by replacing and joining text, never with `str.format`: braces in a JSON schema or in an
issue would break it.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

LAYER_FILES = {"instructions": "20-instructions.md", "tool-policy": "30-tool-policy.md"}
_TOOL_SECTION = re.compile(r"^### (\S+)\s*$", re.MULTILINE)
_EXTERNAL_PREFIX = "mcp__"
_TAG = re.compile(r"<(/?)untrusted_issue", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    system: str
    hash: str  # sha256 of the system prompt and the tools it goes with: what the run was told, as one number
    layers: tuple[str, ...]  # which layers went in, in order


def load_layers() -> dict[str, str]:
    """The text of each static layer, read from the package."""
    folder = resources.files("prova.prompts")
    return {name: (folder / file).read_text(encoding="utf-8") for name, file in LAYER_FILES.items()}


def render_system(
    *,
    tools: Sequence[Mapping[str, Any]],
    contract_text: str | None,
    layers: Mapping[str, str] | None = None,
) -> RenderedPrompt:
    """The system prompt for a run that is offered `tools` (the list the API will get) and, if it has one, a
    contract. `layers` replaces the text of the static layers; tests use it, a run does not."""
    texts = dict(layers) if layers is not None else load_layers()
    names = [str(tool["name"]) for tool in tools]
    parts: list[tuple[str, str]] = [
        ("instructions", texts["instructions"].strip()),
        ("tool-policy", _tool_policy(texts["tool-policy"], names)),
    ]
    if contract_text is not None:
        parts.append(("contract", contract_text.strip()))
    system = "\n\n".join(text for _, text in parts)
    used = tuple(name for name, _ in parts)
    return RenderedPrompt(system=system, hash=_fingerprint(system, tools), layers=used)


def render_user_context(task: str) -> str:
    """The first user message: the task, as data. The tag cannot be opened or closed from inside: the `<` of
    any `untrusted_issue` tag in the text becomes `&lt;`, whatever its case. The rest is kept as it is."""
    safe = _TAG.sub(lambda match: f"&lt;{match.group(1)}untrusted_issue", task)
    return f"<untrusted_issue>\n{safe}\n</untrusted_issue>"


def _tool_policy(text: str, names: Sequence[str]) -> str:
    """The introduction, then the paragraph of each tool that is offered. The paragraph of a tool that is not
    offered is dropped, so the model never hears the name. External tools share one paragraph."""
    pieces = _TOOL_SECTION.split(text)
    introduction, sections = pieces[0].strip(), dict(zip(pieces[1::2], pieces[2::2], strict=True))
    wanted = [name for name in names if name in sections]
    if any(name.startswith(_EXTERNAL_PREFIX) for name in names) and _EXTERNAL_PREFIX in sections:
        wanted.append(_EXTERNAL_PREFIX)
    paragraphs = [f"### {name}\n\n{sections[name].strip()}" for name in sorted(set(wanted))]
    return "\n\n".join([introduction, *paragraphs])


def _fingerprint(system: str, tools: Sequence[Mapping[str, Any]]) -> str:
    """What the model is given besides the user turn: the system prompt and the tool definitions."""
    shown = sorted(
        (
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema", {}),
            }
            for t in tools
        ),
        key=lambda entry: str(entry["name"]),
    )
    canonical = json.dumps({"system": system, "tools": shown}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
