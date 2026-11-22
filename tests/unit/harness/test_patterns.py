"""Tests for prova.harness.patterns: gitignore-style path patterns, used for the deny-lists.

Supported syntax: `*` and `?` (never across `/`), and `**` as a whole path segment. No character classes.
A pattern without `/` matches at any depth; a pattern with `/` is anchored at the workspace root.
"""

import pytest

from prova.harness.patterns import PathMatcher


def matches(patterns: list[str], path: str) -> bool:
    return PathMatcher(patterns).matches(path)


# --- A pattern without a slash matches at any depth --------------------------


@pytest.mark.parametrize("path", [".env", "sub/.env", "a/b/c/.env"])
def test_a_plain_name_matches_at_every_depth(path: str) -> None:
    assert matches([".env"], path)


@pytest.mark.parametrize("path", [".envrc", "env", "my.env", "sub/.envrc"])
def test_a_plain_name_must_match_the_whole_name(path: str) -> None:
    assert not matches([".env"], path)


@pytest.mark.parametrize("path", [".env", ".env.local", "sub/.env.production"])
def test_a_trailing_star_covers_the_variants(path: str) -> None:
    assert matches([".env*"], path)
    assert not matches([".env*"], "x.env")


@pytest.mark.parametrize("path", ["key.pem", "certs/server.pem", "a/b/c.pem"])
def test_a_leading_star_matches_by_extension_at_any_depth(path: str) -> None:
    assert matches(["*.pem"], path)


def test_the_name_pattern_also_covers_everything_inside_a_matching_directory() -> None:
    # A directory whose name matches hides its whole content: nothing under `certs.pem/` is readable either.
    assert matches(["*.pem"], "certs.pem/readme.txt")
    assert not matches(["*.pem"], "key.pem.txt")


# --- A pattern with a slash is anchored at the root --------------------------


@pytest.mark.parametrize("path", [".git/config", ".git/hooks/pre-commit"])
def test_a_directory_double_star_matches_everything_inside(path: str) -> None:
    assert matches([".git/**"], path)


def test_a_directory_double_star_is_anchored_and_does_not_match_the_directory_itself() -> None:
    assert not matches([".git/**"], ".git")
    assert not matches([".git/**"], "sub/.git/config")  # anchored: write `**/.git/**` for any depth
    assert matches(["**/.git/**"], "sub/.git/config")


@pytest.mark.parametrize("path", ["a.pem", "x/a.pem", "x/y/z/a.pem"])
def test_a_leading_double_star_matches_zero_or_more_directories(path: str) -> None:
    assert matches(["**/*.pem"], path)


def test_a_middle_double_star_matches_zero_or_more_directories() -> None:
    assert matches(["a/**/b"], "a/b")
    assert matches(["a/**/b"], "a/x/b")
    assert matches(["a/**/b"], "a/x/y/b")
    assert not matches(["a/**/b"], "x/a/b")


def test_anchored_patterns_do_not_match_deeper_down() -> None:
    assert matches(["hidden_tests/**"], "hidden_tests/a.py")
    assert matches(["hidden_tests/**"], "hidden_tests/sub/b.py")
    assert not matches(["hidden_tests/**"], "tests/hidden_tests/a.py")


def test_a_single_star_and_a_question_mark_never_cross_a_slash() -> None:
    assert matches(["docs/*.md"], "docs/a.md")
    assert not matches(["docs/*.md"], "docs/sub/a.md")
    assert not matches(["docs/*.md"], "other/docs/a.md")
    assert matches(["file?.txt"], "file1.txt")
    assert not matches(["file?.txt"], "file12.txt")
    assert not matches(["a?b"], "a/b")


def test_double_star_inside_a_name_behaves_like_a_single_star() -> None:
    assert matches(["a**b"], "axxb")
    assert not matches(["a**b"], "a/x/b")


# --- Literal characters ------------------------------------------------------


def test_regex_characters_and_brackets_are_plain_text() -> None:
    assert matches(["a+b"], "a+b")
    assert not matches(["a+b"], "aab")
    assert matches(["a[1]"], "a[1]")
    assert not matches(["a[1]"], "a1")
    assert matches(["a.b"], "a.b")
    assert not matches(["a.b"], "axb")


# --- Case: a case-insensitive file system treats .ENV and .env as one file ---


def test_matching_ignores_case() -> None:
    assert matches([".env"], ".ENV")
    assert matches([".env"], "Sub/.Env")
    assert matches(["*.PEM"], "key.pem")
    assert matches([".git/**"], ".GIT/Config")


# --- Several patterns, and no patterns ---------------------------------------


def test_a_path_is_denied_when_any_pattern_matches() -> None:
    patterns = [".env", "*.pem", ".git/**"]
    assert matches(patterns, ".git/config")
    assert matches(patterns, "key.pem")
    assert not matches(patterns, "src/app.py")


def test_no_patterns_match_nothing() -> None:
    assert not matches([], ".env")


# --- Inputs we refuse to guess about -----------------------------------------


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret", "a/../b", "./a"])
def test_a_path_that_is_not_normalised_and_relative_is_an_error(path: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        matches([".env"], path)


def test_the_workspace_root_itself_matches_nothing() -> None:
    assert not matches([".env", "**"], "")


def test_an_empty_pattern_is_an_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        PathMatcher([""])


@pytest.mark.parametrize("pattern", ["a/", "a//b", "/a"])
def test_a_pattern_with_an_empty_path_segment_is_an_error(pattern: str) -> None:
    # `a/` means "directories only" in gitignore, and `/a` means "anchored": we do not guess, we refuse.
    with pytest.raises(ValueError, match="empty path segment"):
        PathMatcher([pattern])
