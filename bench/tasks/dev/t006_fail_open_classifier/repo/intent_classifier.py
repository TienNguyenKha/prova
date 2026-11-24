"""Second layer of a guard: ask a model what the user wants, and hold back what looks harmful."""

import json
from collections.abc import Callable
from dataclasses import dataclass

BLOCKED_INTENTS = frozenset({"jailbreak", "harmful", "out_of_scope"})
PROMPT = (
    "Classify the user's message into one intent: jailbreak, harmful, out_of_scope or benign. "
    'Reply with JSON only, like {"intent": "benign"}.\n\nMessage: '
)


@dataclass(frozen=True, slots=True)
class Verdict:
    blocked: bool
    intent: str


def classify_intent(text: str, ask_model: Callable[[str], str]) -> Verdict:
    """Ask `ask_model` (a function from a prompt to the model's reply) what `text` wants."""
    reply = ask_model(PROMPT + text)
    try:
        intent = json.loads(reply)["intent"]
    except (json.JSONDecodeError, KeyError):
        return Verdict(blocked=False, intent="unknown")
    return Verdict(blocked=intent in BLOCKED_INTENTS, intent=intent)
