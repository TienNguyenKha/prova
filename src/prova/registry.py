"""Registry[T], shared by tools, skills, MCP servers, KB sources, scanners and hooks."""

import re
from collections.abc import Iterator
from typing import Protocol

from prova.errors import (
    DuplicateNameError,
    InvalidNameError,
    RegistryFrozenError,
    UnknownNameError,
)

# API tool-name limit: letters, digits, `_`, `-`, at most 64 characters.
# MCP tool names (`mcp__<server>__<tool>`) match this too.
# TODO(M4): re-check against the current Anthropic tool-use docs when writing ToolRegistry.
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Named(Protocol):
    @property
    def name(self) -> str: ...


class Registry[T: Named]:
    """Store of named items: duplicate names raise, can be frozen, iterates in name order."""

    def __init__(self, kind: str) -> None:
        self._kind = kind  # only used so error messages say which registry it is
        self._items: dict[str, T] = {}
        self._frozen = False

    def register(self, item: T) -> None:
        name = item.name
        if self._frozen:
            raise RegistryFrozenError(f"{self._kind} registry is frozen, cannot register {name!r}")
        if not NAME_PATTERN.fullmatch(name):
            raise InvalidNameError(
                f"invalid {self._kind} name {name!r}: use letters, digits, '_' and '-', 1-64 characters"
            )
        if name in self._items:
            raise DuplicateNameError(f"{self._kind} {name!r} is already registered")
        self._items[name] = item

    def freeze(self) -> None:
        self._frozen = True

    def get(self, name: str) -> T:
        """For names that come from our own code: a missing name is a programming error."""
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "(empty)"
            raise UnknownNameError(f"unknown {self._kind} {name!r}. Registered: {known}") from None

    def find(self, name: str) -> T | None:
        """For names that come from the model: a missing name is normal, so return None."""
        return self._items.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[T]:
        return iter([self._items[name] for name in sorted(self._items)])

    def __len__(self) -> int:
        return len(self._items)
