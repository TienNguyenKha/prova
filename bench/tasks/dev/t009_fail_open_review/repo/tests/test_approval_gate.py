from collections.abc import Callable

from approval_gate import review


def says(reply: str) -> Callable[[str], str]:
    return lambda action: reply


def test_an_action_the_reviewer_approves_goes_ahead() -> None:
    assert review("deploy to staging", says('{"verdict": "approved"}')).approved is True


def test_an_action_the_reviewer_rejects_is_held_back() -> None:
    assert review("drop the database", says('{"verdict": "rejected"}')).approved is False


def test_an_action_is_not_approved_when_the_reviewer_cannot_be_reached() -> None:
    def unreachable(action: str) -> str:
        raise ConnectionError("the reviewer is down")

    assert review("deploy to production", unreachable).approved is False
