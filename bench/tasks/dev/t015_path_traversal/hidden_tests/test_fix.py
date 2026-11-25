"""Behaviour of safe_join: a path is joined only if what it really points to is inside the base folder."""

import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def module(load: Callable[[str], ModuleType]) -> ModuleType:
    return load("safe_paths")


@pytest.fixture
def base(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    (folder / "docs").mkdir(parents=True)
    (folder / "docs" / "a.txt").write_text("a", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    return folder


def real(path: str | Path) -> str:
    return os.path.realpath(path)


@pytest.mark.parametrize("sibling", ["data-evil", "data2", "data_backup", "data.old"])
@pytest.mark.parametrize("trailing", ["", "/"])
def test_a_folder_next_to_the_base_with_the_same_start_is_outside(
    module: ModuleType, base: Path, sibling: str, trailing: str
) -> None:
    (base.parent / sibling).mkdir()
    with pytest.raises(module.UnsafePathError):
        module.safe_join(str(base) + trailing, f"../{sibling}/x.txt")


@pytest.mark.parametrize(
    ("links", "user_path"),
    [
        ({"link": "../outside"}, "link/secret.txt"),  # a folder link
        ({"secret.txt": "../outside/secret.txt"}, "secret.txt"),  # a file link
        ({"a": "b", "b": "../outside"}, "a/secret.txt"),  # a link to a link
        ({"docs/deep": "../../outside"}, "docs/deep/secret.txt"),  # a link further down
        ({"root": "/"}, "root/etc/passwd"),  # a link to an absolute path
    ],
)
def test_a_link_that_points_out_of_the_base_is_refused(
    module: ModuleType, base: Path, links: dict[str, str], user_path: str
) -> None:
    for name, target in links.items():
        (base / name).symlink_to(target)
    with pytest.raises(module.UnsafePathError):
        module.safe_join(str(base), user_path)


def test_a_path_with_a_nul_byte_is_refused(module: ModuleType, base: Path) -> None:
    with pytest.raises(module.UnsafePathError):
        module.safe_join(str(base), "docs/a.txt\x00.png")


@pytest.mark.parametrize("user_path", ["../secret.txt", "/etc/passwd", "docs/../../outside/secret.txt", ".."])
def test_a_path_that_goes_up_out_of_the_base_is_refused(
    module: ModuleType, base: Path, user_path: str
) -> None:
    with pytest.raises(module.UnsafePathError):
        module.safe_join(str(base), user_path)


@pytest.mark.parametrize(
    ("user_path", "inside"),
    [("docs/a.txt", "docs/a.txt"), ("docs/../docs/a.txt", "docs/a.txt"), ("new/file.txt", "new/file.txt")],
)
def test_a_path_inside_the_base_is_joined_even_if_it_does_not_exist_yet(
    module: ModuleType, base: Path, user_path: str, inside: str
) -> None:
    assert real(module.safe_join(str(base), user_path)) == real(base / inside)


@pytest.mark.parametrize("user_path", ["", "."])
def test_the_base_itself_is_inside(module: ModuleType, base: Path, user_path: str) -> None:
    assert real(module.safe_join(str(base), user_path)) == real(base)


def test_a_link_that_stays_inside_the_base_is_followed(module: ModuleType, base: Path) -> None:
    (base / "shortcut").symlink_to("docs")
    assert real(module.safe_join(str(base), "shortcut/a.txt")) == real(base / "docs" / "a.txt")


def test_a_refused_path_raises_an_error_that_is_a_value_error(module: ModuleType) -> None:
    assert issubclass(module.UnsafePathError, ValueError)
