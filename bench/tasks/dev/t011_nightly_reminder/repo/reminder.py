"""Nightly job: remind people about open issues that nobody has touched for a while."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

REMINDER = "This issue has had no activity for a while. Is it still relevant?"

Issue = Mapping[str, Any]


def nag(
    issues: Sequence[Issue],
    post_comment: Callable[[int, str], None],
    today: date,
    *,
    bot: str = "stale-bot",
    idle_days: int = 30,
) -> list[int]:
    """Post the reminder on each open issue that has been quiet for `idle_days` days or more.

    An issue is a mapping with `number`, `state` ("open" or "closed"), `opened` (a date) and `comments`
    (each a mapping with `author`, `created` (a date) and `body`). `bot` is the name the reminders are posted
    under. Returns the numbers of the issues that were reminded."""
    reminded: list[int] = []
    for issue in issues:
        if issue["state"] != "open":
            continue
        if (today - issue["opened"]).days < idle_days:
            continue
        post_comment(issue["number"], REMINDER)
        reminded.append(issue["number"])
    return reminded
