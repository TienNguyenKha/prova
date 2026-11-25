"""Behaviour of nag: one reminder, then silence until a person answers and the issue goes quiet again."""

from collections.abc import Callable
from datetime import date, timedelta
from types import ModuleType
from typing import Any

import pytest

TODAY = date(2026, 3, 1)


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def issue(
    number: int = 7, *, opened: int = 90, state: str = "open", comments: list[Any] | None = None
) -> Any:
    return {"number": number, "state": state, "opened": ago(opened), "comments": comments or []}


def comment(author: str, days_ago: int, body: str = "text") -> dict[str, Any]:
    return {"author": author, "created": ago(days_ago), "body": body}


def no_post(number: int, body: str) -> None:
    pytest.fail(f"posted on issue {number}")


@pytest.fixture
def module(load: Callable[[str], ModuleType]) -> ModuleType:
    return load("reminder")


class Posts:
    """Stands in for the tracker: remembers what was posted and puts it in the issue as a comment."""

    def __init__(self, issues: list[Any], bot: str = "stale-bot") -> None:
        self.issues = {i["number"]: i for i in issues}
        self.bot = bot
        self.posted: list[tuple[int, str]] = []
        self.today = TODAY

    def __call__(self, number: int, body: str) -> None:
        self.posted.append((number, body))
        self.issues[number]["comments"].append({"author": self.bot, "created": self.today, "body": body})


def test_a_quiet_open_issue_gets_one_reminder_and_is_returned(module: ModuleType) -> None:
    posts = Posts([issue(3)])
    assert module.nag(list(posts.issues.values()), posts, TODAY) == [3]
    assert posts.posted == [(3, module.REMINDER)]


def test_closed_issues_and_issues_quiet_for_less_than_the_limit_get_nothing(module: ModuleType) -> None:
    posts = Posts([issue(1, state="closed"), issue(2, opened=29)])
    assert module.nag(list(posts.issues.values()), posts, TODAY) == []
    assert posts.posted == []


def test_an_issue_quiet_for_exactly_the_limit_gets_the_reminder(module: ModuleType) -> None:
    posts = Posts([issue(1, opened=30), issue(2, opened=10)])
    assert module.nag(list(posts.issues.values()), posts, TODAY, idle_days=10) == [1, 2]


def test_every_night_after_the_first_posts_nothing(module: ModuleType) -> None:
    posts = Posts([issue(5)])
    issues = list(posts.issues.values())
    module.nag(issues, posts, TODAY)
    for night in range(1, 6):
        posts.today = TODAY + timedelta(days=night)
        assert module.nag(issues, posts, posts.today) == []
    assert len(posts.posted) == 1


def test_an_issue_the_bot_already_reminded_is_left_alone_even_by_a_fresh_run(module: ModuleType) -> None:
    reminded = issue(4, comments=[comment("stale-bot", 20, module.REMINDER)])
    assert module.nag([reminded], no_post, TODAY) == []


def test_a_recent_comment_from_a_person_keeps_an_old_issue_from_being_reminded(module: ModuleType) -> None:
    active = issue(6, opened=100, comments=[comment("ana", 5)])
    assert module.nag([active], no_post, TODAY) == []


def test_the_reminder_comes_again_after_a_person_answers_and_the_issue_goes_quiet(module: ModuleType) -> None:
    comments = [comment("stale-bot", 60, module.REMINDER), comment("ana", 40, "still wanted")]
    posts = Posts([issue(8, opened=100, comments=comments)])
    assert module.nag(list(posts.issues.values()), posts, TODAY) == [8]


def test_the_reminder_does_not_come_again_while_the_answer_is_still_recent(module: ModuleType) -> None:
    comments = [comment("stale-bot", 60, module.REMINDER), comment("ana", 10, "still wanted")]
    assert module.nag([issue(8, opened=100, comments=comments)], no_post, TODAY) == []


def test_a_person_who_quotes_the_reminder_is_not_the_bot(module: ModuleType) -> None:
    quoted = [comment("ana", 40, f"> {module.REMINDER}\nyes, it is")]
    posts = Posts([issue(9, opened=100, comments=quoted)])
    assert module.nag(list(posts.issues.values()), posts, TODAY) == [9]


def test_the_name_of_the_bot_can_be_chosen(module: ModuleType) -> None:
    mine = issue(2, comments=[comment("tidy-bot", 20, module.REMINDER)])
    assert module.nag([mine], no_post, TODAY, bot="tidy-bot") == []
    other = issue(3, comments=[comment("tidy-bot", 40, module.REMINDER)])
    posts = Posts([other])
    assert module.nag([other], posts, TODAY) == [3]  # a comment by tidy-bot is not by the default bot
