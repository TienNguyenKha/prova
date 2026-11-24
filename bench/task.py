"""A benchmark task: the `task.yaml` of a task folder, loaded strictly.

The file is written by a person and it decides what counts as a pass, so a wrong one is refused with a message
that says which field and why. Nothing is ignored: a field nobody knows is an error, not a comment.
"""

from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

HIDDEN_TESTS = "hidden_tests/"


class TaskError(Exception):
    """A task folder or its task.yaml is wrong. The message names the folder, the field and the reason."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Budget(_Strict):
    max_tool_calls: int = Field(gt=0)
    max_usd: float = Field(gt=0)
    max_seconds: float = Field(gt=0)


class Localization(_Strict):
    files: tuple[str, ...]
    symbols: tuple[str, ...] = ()

    @field_validator("files")
    @classmethod
    def _files_stay_in_the_repo(cls, files: tuple[str, ...]) -> tuple[str, ...]:
        for file in files:
            path = PurePosixPath(file)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"{file!r} must be a relative path inside repo/")
        return files


class Task(_Strict):
    id: str
    benches: tuple[Literal["fix", "review", "security", "vuln"], ...] = Field(min_length=1)
    split: Literal["dev", "heldout"]
    difficulty: Literal["easy", "medium", "hard"]
    source: str = ""
    issue: str
    fail_to_pass: tuple[str, ...] = Field(min_length=1)  # must FAIL on the original code, PASS after the fix
    pass_to_pass: tuple[str, ...] = Field(min_length=1)  # must PASS before and after: catches a bad "fix"
    localization: Localization
    forbidden_paths: tuple[str, ...] = (HIDDEN_TESTS,)
    forbidden_tools: tuple[str, ...] = ()
    budget: Budget
    tags: tuple[str, ...] = ()
    notes: str = ""

    @field_validator("issue")
    @classmethod
    def _issue_says_something(cls, issue: str) -> str:
        if not issue.strip():
            raise ValueError("the issue is empty: it is the only thing the agent is told")
        return issue

    @field_validator("fail_to_pass", "pass_to_pass")
    @classmethod
    def _each_test_is_one_test_in_hidden_tests(cls, tests: tuple[str, ...]) -> tuple[str, ...]:
        for test in tests:
            if not _is_hidden_test(test):
                raise ValueError(f"{test!r} must be hidden_tests/<file>::<test name>, inside hidden_tests/")
        return tests

    @field_validator("forbidden_paths")
    @classmethod
    def _the_hidden_tests_are_always_forbidden(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        return paths if HIDDEN_TESTS in paths else (HIDDEN_TESTS, *paths)

    @model_validator(mode="after")
    def _no_test_in_both_lists(self) -> "Task":
        both = sorted(set(self.fail_to_pass) & set(self.pass_to_pass))
        if both:
            raise ValueError(f"a test cannot be in both fail_to_pass and pass_to_pass: {both}")
        return self


def _is_hidden_test(test: str) -> bool:
    file, separator, name = test.partition("::")
    path = PurePosixPath(file)
    inside = not path.is_absolute() and ".." not in path.parts and path.parts[:1] == ("hidden_tests",)
    return bool(separator and name.strip() and inside and len(path.parts) >= 2)


def load_task(folder: Path) -> Task:
    """Load and check one task folder: task.yaml, repo/ and hidden_tests/ must all be there."""
    spec = folder / "task.yaml"
    if not spec.is_file():
        raise TaskError(f"{folder}: there is no task.yaml")
    data = _read_yaml(spec)
    try:
        task = Task.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(f"{_where(error['loc'])}: {error['msg']}" for error in e.errors())
        raise TaskError(f"{spec}: {problems}") from None
    if task.id != folder.name:
        raise TaskError(f"{spec}: the id {task.id!r} must be the name of the folder, {folder.name!r}")
    for part in ("repo", "hidden_tests"):
        if not (folder / part).is_dir():
            raise TaskError(f"{folder}: there is no {part}/ folder")
    return task


def _read_yaml(spec: Path) -> dict[str, Any]:
    try:
        data: object = yaml.safe_load(spec.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TaskError(f"{spec}: not valid YAML ({type(e).__name__})") from None
    if not isinstance(data, dict):
        raise TaskError(f"{spec}: task.yaml must be a mapping of fields")
    return cast("dict[str, Any]", data)


def _where(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location) or "task"
