"""Behaviour of resolve_inputs: which two images are compared, and when the command line is refused."""

from collections.abc import Callable
from types import ModuleType

import pytest


@pytest.fixture
def module(load: Callable[[str], ModuleType]) -> ModuleType:
    return load("screenshot_args")


class Camera:
    """A stand-in for the browser: remembers which pages it was asked to photograph."""

    def __init__(self) -> None:
        self.pages: list[str] = []

    def __call__(self, url: str) -> str:
        self.pages.append(url)
        return "captured.png"


@pytest.mark.parametrize("url", ["http://localhost:3000", "https://example.test/page?tab=2"])
def test_a_url_is_captured_and_compared_with_the_expected_image(module: ModuleType, url: str) -> None:
    camera = Camera()
    assert module.resolve_inputs([url, "expected.png"], camera) == ("expected.png", "captured.png")
    assert camera.pages == [url]


def test_two_image_files_are_compared_in_the_order_given_and_nothing_is_captured(module: ModuleType) -> None:
    camera = Camera()
    assert module.resolve_inputs(["expected.png", "actual.png"], camera) == ("expected.png", "actual.png")
    assert camera.pages == []


@pytest.mark.parametrize(
    "args", [[], ["only-one.png"], ["a.png", "b.png", "c.png"], ["http://x.test", "a", "b"]]
)
def test_any_other_number_of_arguments_is_a_usage_error(module: ModuleType, args: list[str]) -> None:
    camera = Camera()
    with pytest.raises(module.UsageError):
        module.resolve_inputs(args, camera)
    assert camera.pages == []
