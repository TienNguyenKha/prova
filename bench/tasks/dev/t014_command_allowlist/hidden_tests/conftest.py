"""Load the module under test from the task's repo/, the copy the agent worked on."""

import importlib.util
import pathlib
import types
from collections.abc import Callable

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent / "repo"


@pytest.fixture
def load() -> Callable[[str], types.ModuleType]:
    def _load(module_name: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(module_name, REPO / f"{module_name}.py")
        assert spec is not None and spec.loader is not None, f"no module {module_name} in repo/"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load
