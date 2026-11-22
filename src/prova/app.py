"""Composition root: the one place where Prova's parts are created and wired together."""

from dataclasses import dataclass

from prova.config import Settings
from prova.llm.client import LLMClient, make_client


@dataclass(frozen=True, slots=True)
class App:
    settings: Settings
    llm: LLMClient
    # TODO(M4): tools (ToolRegistry). Later: sandbox (M7), hooks (M8), mcp (M8b), kb (S1), scanners (S3).


def build_app(settings: Settings, *, llm: LLMClient | None = None) -> App:
    """Use the given `llm` (tests pass a fake) or build the real client, and wire the App."""
    # TODO(M4): register tools here, then freeze the registry before returning.
    return App(
        settings=settings,
        llm=llm if llm is not None else make_client(settings),
    )
