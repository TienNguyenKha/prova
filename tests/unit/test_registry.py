"""Tests for prova.registry: duplicates, invalid names, freeze, unknown names, stable order."""

from dataclasses import dataclass

import pytest

from prova.errors import (
    DuplicateNameError,
    InvalidNameError,
    RegistryError,
    RegistryFrozenError,
    UnknownNameError,
)
from prova.registry import Registry


@dataclass(frozen=True)
class Item:
    """Minimal fake: having a `name` is enough to satisfy the Named Protocol (no inheritance needed)."""

    name: str
    tag: str = ""


def make_registry(*names: str) -> Registry[Item]:
    registry = Registry[Item]("tool")
    for name in names:
        registry.register(Item(name))
    return registry


# --- Success paths ----------------------------------------------------------


def test_register_then_get_returns_the_same_item() -> None:
    registry = Registry[Item]("tool")
    item = Item("read_file")
    registry.register(item)
    assert registry.get("read_file") is item


def test_contains_and_len() -> None:
    registry = make_registry("a", "b")
    assert "a" in registry
    assert "zzz" not in registry
    assert len(registry) == 2


def test_find_returns_item_or_none() -> None:
    registry = make_registry("a")
    assert registry.find("a") == Item("a")
    assert registry.find("missing") is None


def test_iteration_is_sorted_by_name_regardless_of_insertion_order() -> None:
    registry = make_registry("zeta", "alpha", "mid")
    assert [item.name for item in registry] == ["alpha", "mid", "zeta"]


@pytest.mark.parametrize("name", ["read_file", "a", "run-tests", "mcp__github__create_issue", "A1_b-2"])
def test_valid_names_are_accepted(name: str) -> None:
    registry = make_registry(name)
    assert name in registry


# --- Error paths (as important as the success paths) ------------------------


def test_duplicate_name_raises_and_does_not_overwrite() -> None:
    registry = Registry[Item]("tool")
    original = Item("read_file", tag="original")
    registry.register(original)

    with pytest.raises(DuplicateNameError, match=r"tool.*read_file"):
        registry.register(Item("read_file", tag="shadow"))

    assert registry.get("read_file") is original  # the original is intact, not overwritten


@pytest.mark.parametrize("name", ["", "has space", "a.b", "naïve", "x" * 65, "a/b"])
def test_invalid_name_raises(name: str) -> None:
    registry = Registry[Item]("tool")
    with pytest.raises(InvalidNameError, match="tool"):
        registry.register(Item(name))
    assert len(registry) == 0


def test_register_after_freeze_raises() -> None:
    registry = make_registry("a")
    registry.freeze()
    with pytest.raises(RegistryFrozenError, match="tool"):
        registry.register(Item("b"))
    assert "b" not in registry


def test_reads_still_work_after_freeze() -> None:
    registry = make_registry("b", "a")
    registry.freeze()
    assert registry.get("a") == Item("a")
    assert [item.name for item in registry] == ["a", "b"]


def test_get_unknown_name_raises_with_kind_and_name() -> None:
    registry = make_registry("a")
    with pytest.raises(UnknownNameError, match=r"tool.*missing"):
        registry.get("missing")


def test_kind_appears_in_messages_for_other_registries() -> None:
    skills = Registry[Item]("skill")
    with pytest.raises(UnknownNameError, match="skill"):
        skills.get("x")


@pytest.mark.parametrize(
    "error", [DuplicateNameError, InvalidNameError, RegistryFrozenError, UnknownNameError]
)
def test_all_registry_errors_share_one_base(error: type[Exception]) -> None:
    assert issubclass(error, RegistryError)
