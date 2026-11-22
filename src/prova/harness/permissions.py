"""What a run may do (profiles) and what it may touch (the deny-list).

A bad configuration here must BLOCK the run, never relax it: an unknown key, an unknown permission, a missing
`allow` or an unknown profile name is a ConfigError, and there is no profile to fall back to.
"""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from prova.errors import ConfigError
from prova.harness.patterns import PathMatcher
from prova.validation import describe_errors


class Permission(StrEnum):
    """What a tool is allowed to do. The profile of a run decides which of these the model may use."""

    READ = "read"
    WRITE = "write"
    EXEC = "exec"
    NETWORK = "network"


# Patterns follow prova.harness.patterns: a name without `/` matches at any depth, one with `/` is anchored
# at the workspace root. Not matched here on purpose: `.git/HEAD` and the rest of `.git` (only its config
# can hold a token), and ordinary source files such as `keyboard.py`.
# Known cost: `.env*` also hides `.env.example`, and `credentials*` hides a file named `credentials.py`.
DEFAULT_DENY_PATHS: tuple[str, ...] = (
    ".env*",
    "id_rsa*",
    "*.key",
    "*.pem",
    "credentials*",
    ".netrc",
    ".git/config",
)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allow: frozenset[Permission]  # required: there is no default permission
    deny_paths: tuple[str, ...] = ()  # added to DEFAULT_DENY_PATHS, never instead of it
    write_paths: tuple[str, ...] = ()  # where the write tools may write (used from M6); empty: nowhere

    @field_validator("deny_paths", "write_paths")
    @classmethod
    def _patterns_are_valid(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        PathMatcher(patterns)  # a bad pattern is found when the file is loaded, not on the first request
        return patterns

    @property
    def denied_paths(self) -> tuple[str, ...]:
        """The default deny-list plus this profile's own: a profile cannot forget to protect the secrets."""
        return (*DEFAULT_DENY_PATHS, *self.deny_paths)


class _ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: dict[str, Profile]


def load_profiles(path: Path) -> dict[str, Profile]:
    """Read permissions.yaml. Anything wrong with it is a ConfigError that names the file and the problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read the permissions file {path}: {e.strerror or e}") from e
    try:
        raw = yaml.safe_load(text)  # safe_load: a YAML file must never construct objects or run code
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML ({type(e).__name__}); nothing was loaded") from e
    if not isinstance(raw, dict) or "profiles" not in raw:
        raise ConfigError(f"{path} must be a mapping with a 'profiles' key")
    try:
        parsed = _ProfilesFile.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path} has an invalid profile: {describe_errors(e)}") from e
    if not parsed.profiles:
        raise ConfigError(f"{path} defines no profiles")
    return parsed.profiles


def get_profile(profiles: Mapping[str, Profile], name: str) -> Profile:
    """One profile by name. An unknown name is an error: there is never a fallback to some other profile."""
    try:
        return profiles[name]
    except KeyError:
        known = ", ".join(sorted(profiles))
        raise ConfigError(f"unknown profile {name!r}. Known profiles: {known}") from None
