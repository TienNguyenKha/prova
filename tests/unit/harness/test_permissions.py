"""Tests for prova.harness.permissions: the default deny-list protects secrets, not ordinary code."""

import pytest

from prova.harness.patterns import PathMatcher
from prova.harness.permissions import DEFAULT_DENY_PATHS

DEFAULT = PathMatcher(DEFAULT_DENY_PATHS)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "sub/.env.production",
        "id_rsa",
        ".ssh/id_rsa",
        "id_rsa.pub",
        "server.pem",
        "tls/private.key",
        "credentials.json",
        "aws/credentials",
        ".netrc",
        ".git/config",
        ".ENV",  # case does not matter on a case-insensitive file system
    ],
)
def test_the_usual_secret_files_are_denied_by_default(path: str) -> None:
    assert DEFAULT.matches(path)


@pytest.mark.parametrize(
    "path",
    [".gitignore", ".git/HEAD", "src/app.py", "environment.md", "README.md", "monkey.txt", "keyboard.py"],
)
def test_ordinary_files_are_not_denied(path: str) -> None:
    assert not DEFAULT.matches(path)
