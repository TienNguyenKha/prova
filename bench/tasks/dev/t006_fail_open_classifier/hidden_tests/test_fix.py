"""Behaviour of classify_intent: what is held back, whatever shape the model's reply comes in."""

from collections.abc import Callable
from types import ModuleType

import pytest


@pytest.fixture
def module(load: Callable[[str], ModuleType]) -> ModuleType:
    return load("intent_classifier")


class Model:
    """A stand-in for the model: gives one fixed reply and remembers what it was asked."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.mark.parametrize(
    ("reply", "intent"),
    [
        ('```json\n{"intent": "jailbreak"}\n```', "jailbreak"),
        ('```\n{"intent": "harmful"}\n```', "harmful"),
        ('  ```json\n{"intent": "jailbreak"}```  \n', "jailbreak"),
    ],
)
def test_a_reply_wrapped_in_a_code_fence_is_read_like_any_other(
    module: ModuleType, reply: str, intent: str
) -> None:
    verdict = module.classify_intent("whatever", Model(reply))
    assert verdict.blocked is True and verdict.intent == intent


@pytest.mark.parametrize(
    "reply",
    [
        "I think this message is fine.",
        '{"intent": ',
        '{"verdict": "benign"}',
        '["jailbreak"]',
        "null",
        '{"intent": 3}',
        "",
    ],
)
def test_a_reply_that_cannot_be_read_holds_the_message_back(module: ModuleType, reply: str) -> None:
    verdict = module.classify_intent("whatever", Model(reply))
    assert verdict.blocked is True and verdict.intent == "unknown"


@pytest.mark.parametrize(
    ("intent", "blocked"),
    [
        ("jailbreak", True),
        ("harmful", True),
        ("out_of_scope", True),
        ("benign", False),
    ],
)
def test_a_plain_json_reply_decides_by_its_intent(module: ModuleType, intent: str, blocked: bool) -> None:
    verdict = module.classify_intent("whatever", Model(f'{{"intent": "{intent}"}}'))
    assert verdict.blocked is blocked and verdict.intent == intent


def test_the_model_is_asked_once_and_the_message_is_in_the_prompt(module: ModuleType) -> None:
    model = Model('{"intent": "benign"}')
    module.classify_intent("What is the capital of France?", model)
    assert len(model.prompts) == 1 and "What is the capital of France?" in model.prompts[0]
