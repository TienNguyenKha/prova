"""Work out which two images a screenshot comparison tool compares, from its command line."""

from collections.abc import Callable, Sequence

USAGE = "usage: EXPECTED.png ACTUAL.png   or   URL EXPECTED.png"


class UsageError(ValueError):
    """The command line is not one of the two forms the tool understands."""


def resolve_inputs(args: Sequence[str], capture: Callable[[str], str]) -> tuple[str, str]:
    """Return (expected, actual): the paths of the two images to compare.

    `capture(url)` takes a screenshot of the page at `url` and returns the path of the image it saved.
    """
    if len(args) == 2:
        # Two image files: the expected one, then the actual one.
        return args[0], args[1]
    if len(args) == 2 and args[0].startswith("http"):
        # A URL, then the expected image: the actual image is a fresh screenshot of the page.
        return args[1], capture(args[0])
    raise UsageError(USAGE)
