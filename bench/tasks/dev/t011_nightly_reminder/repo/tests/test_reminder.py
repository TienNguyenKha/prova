from datetime import date, timedelta
from typing import Any

from reminder import REMINDER, nag

TODAY = date(2026, 3, 1)


def an_old_issue() -> dict[str, Any]:
    return {"number": 7, "state": "open", "opened": TODAY - timedelta(days=90), "comments": []}


def test_a_quiet_open_issue_gets_the_reminder() -> None:
    posted: list[tuple[int, str]] = []
    nag([an_old_issue()], lambda number, body: posted.append((number, body)), TODAY)
    assert posted == [(7, REMINDER)]


def test_a_closed_issue_gets_nothing() -> None:
    issue = {**an_old_issue(), "state": "closed"}
    posted: list[tuple[int, str]] = []
    nag([issue], lambda number, body: posted.append((number, body)), TODAY)
    assert posted == []


def test_the_second_night_does_not_post_the_reminder_again() -> None:
    issue = an_old_issue()
    posted: list[int] = []

    def post(number: int, body: str) -> None:
        posted.append(number)
        issue["comments"].append({"author": "stale-bot", "created": TODAY, "body": body})

    nag([issue], post, TODAY)
    nag([issue], post, TODAY + timedelta(days=1))
    assert posted == [7]
