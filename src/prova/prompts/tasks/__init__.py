"""The prompts of a task: the words a run is given after the issue, kept as templates next to the layers of
the system prompt, so that they are versioned and hashed like it.

A template has places written `{name}`. They are filled in one pass: a value is put in once and never read
again, so braces in it (an issue, a traceback, a JSON schema) stay as they are; `str.format` is never used.
A value that is missing or that the template has no place for is an error. The hash of a template goes into
the config of the agents that use it, so a result says which words its runs were given.
"""

import hashlib
import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

TASK_PROMPTS = ("fix_goal", "fix_feedback", "repro", "adversary", "self_review")
_PLACE = re.compile(r"\{([a-z_]+)\}")


class TaskPromptError(Exception):
    """An unknown template, a value it needs that was not given, or a value it has no place for."""


def task_prompt(name: str, *, templates: Path | None = None, **values: str) -> str:
    """The template `name` with its places filled by `values`, without the newline at the end of the file."""
    template = _read(name, templates)
    places = set(_PLACE.findall(template))
    missing, unknown = sorted(places - values.keys()), sorted(values.keys() - places)
    if missing or unknown:
        raise TaskPromptError(f"task prompt {name!r}: missing {missing}, unknown {unknown}")
    return _PLACE.sub(lambda match: values[match.group(1)], template).rstrip("\n")


def task_prompt_hash(name: str, *, templates: Path | None = None) -> str:
    """The sha256 of the template: any change of its words changes it."""
    return hashlib.sha256(_read(name, templates).encode("utf-8")).hexdigest()


def task_prompt_hashes(*names: str) -> dict[str, str]:
    """The hashes of the templates an agent uses, for its config."""
    return {name: task_prompt_hash(name) for name in names}


def _read(name: str, templates: Path | None) -> str:
    if name not in TASK_PROMPTS:
        raise TaskPromptError(f"no task prompt {name!r}; known: {', '.join(TASK_PROMPTS)}")
    source: Traversable | Path = (
        templates if templates is not None else resources.files("prova.prompts.tasks")
    )
    return (source / f"{name}.md").read_text(encoding="utf-8")
