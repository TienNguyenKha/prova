"""Behaviour of review: an action goes ahead only when the reviewer clearly approves it."""

from collections.abc import Callable
from types import ModuleType

import pytest


@pytest.fixture
def module(load: Callable[[str], ModuleType]) -> ModuleType:
    return load("approval_gate")


class Reviewer:
    """A stand-in for the reviewer: gives one fixed reply, or fails, and remembers what it was asked."""

    def __init__(self, reply: object = None, fails_with: Exception | None = None) -> None:
        self.reply = reply
        self.fails_with = fails_with
        self.actions: list[str] = []

    def __call__(self, action: str) -> object:
        self.actions.append(action)
        if self.fails_with is not None:
            raise self.fails_with
        return self.reply


@pytest.mark.parametrize(
    "failure",
    [ConnectionError("the reviewer is down"), TimeoutError("no answer"), RuntimeError("500 from it")],
)
def test_a_reviewer_that_cannot_be_reached_does_not_approve(module: ModuleType, failure: Exception) -> None:
    decision = module.review("deploy to production", Reviewer(fails_with=failure))
    assert decision.approved is False
    assert isinstance(decision.reason, str) and decision.reason


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "approved",  # text, but not JSON
        "not json at all",
        "null",
        "[]",
        '"approved"',  # JSON, but not an object
        '["approved"]',
        "{}",
        '{"verdicts": "approved"}',  # no verdict
        '{"verdict": null}',
        '{"verdict": 1}',
        '{"verdict": true}',
        '{"verdict": ["approved"]}',  # a verdict that is not text
        None,  # no reply at all
    ],
)
def test_a_reply_that_cannot_be_read_does_not_approve(module: ModuleType, reply: object) -> None:
    decision = module.review("deploy to production", Reviewer(reply))
    assert decision.approved is False
    assert isinstance(decision.reason, str) and decision.reason


def test_an_action_the_reviewer_approves_goes_ahead(module: ModuleType) -> None:
    decision = module.review("deploy to staging", Reviewer('{"verdict": "approved"}'))
    assert decision.approved is True


@pytest.mark.parametrize("verdict", ["rejected", "needs_changes", ""])
def test_a_verdict_other_than_approved_holds_the_action_back(module: ModuleType, verdict: str) -> None:
    decision = module.review("drop the table", Reviewer(f'{{"verdict": "{verdict}"}}'))
    assert decision.approved is False


def test_the_reviewer_is_asked_once_with_the_text_of_the_action(module: ModuleType) -> None:
    reviewer = Reviewer('{"verdict": "approved"}')
    module.review("rotate the keys", reviewer)
    assert reviewer.actions == ["rotate the keys"]
