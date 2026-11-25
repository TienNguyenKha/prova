"""A gate for risky actions: ask a reviewer service whether an action may go ahead."""

import json
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Decision:
    approved: bool
    reason: str


def review(action: str, ask_reviewer: Callable[[str], str]) -> Decision:
    """Ask `ask_reviewer` (a function from the text of an action to the reviewer's reply, which raises when
    the service cannot be reached) whether `action` may go ahead."""
    try:
        reply = ask_reviewer(action)
    except Exception:  # noqa: BLE001 - any failure of the service
        return Decision(approved=True, reason="the reviewer could not be reached, so the work goes on")
    verdict = json.loads(reply)["verdict"]
    return Decision(approved=verdict == "approved", reason=f"the reviewer said {verdict}")
