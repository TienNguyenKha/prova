import os
from pathlib import Path

import pytest
from safe_paths import UnsafePathError, safe_join


@pytest.fixture
def base(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    (folder / "docs").mkdir(parents=True)
    (folder / "docs" / "a.txt").write_text("a", encoding="utf-8")
    return folder


def same(a: str | Path, b: str | Path) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


def test_a_path_inside_the_base_is_joined(base: Path) -> None:
    assert same(safe_join(str(base), "docs/a.txt"), base / "docs" / "a.txt")


def test_a_path_that_goes_up_and_comes_back_inside_is_joined(base: Path) -> None:
    assert same(safe_join(str(base), "docs/../docs/a.txt"), base / "docs" / "a.txt")


@pytest.mark.parametrize("user_path", ["../secret.txt", "/etc/passwd", "docs/../../secret.txt"])
def test_a_path_that_leads_out_of_the_base_is_refused(base: Path, user_path: str) -> None:
    with pytest.raises(UnsafePathError):
        safe_join(str(base), user_path)


def test_a_folder_next_to_the_base_whose_name_starts_the_same_is_outside(base: Path) -> None:
    (base.parent / "data-evil").mkdir()
    with pytest.raises(UnsafePathError):
        safe_join(str(base), "../data-evil/x.txt")


def test_a_link_inside_the_base_that_points_out_of_it_is_refused(base: Path) -> None:
    outside = base.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    (base / "link").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        safe_join(str(base), "link/secret.txt")
