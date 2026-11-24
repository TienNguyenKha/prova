"""The final report of the agent, as data: what it changed and why, how to run the tests, what it assumed.

The model hands this in through the `submit_result` tool, so it arrives already checked (Pydantic) and nobody
has to read it out of prose. The stop hook then compares it with what really happened.
"""

import posixpath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_TEXT_CHARS = 300
MAX_ASSUMPTIONS = 20


def clean_path(value: str) -> str:
    """The path in one normal form (`./src//a.py` is `src/a.py`), or an error if it leaves the workspace."""
    if value.startswith("/") or "\\" in value or (len(value) > 1 and value[1] == ":"):
        raise ValueError("path must be relative to the workspace")
    normal = posixpath.normpath(value)
    if normal == ".":
        raise ValueError("path must name a file, not the workspace itself")
    if normal == ".." or normal.startswith("../"):
        raise ValueError("path must stay inside the workspace")
    return normal


class FileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=500, description="Path of the file, relative to the workspace")
    reason: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, description="Why you changed it, one line")

    @field_validator("path")
    @classmethod
    def _inside_the_workspace(cls, value: str) -> str:
        return clean_path(value)


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    files_changed_with_reason: tuple[FileChange, ...] = Field(
        description="Every file you changed, each with the reason. An empty list if you changed none"
    )
    test_command: str = Field(min_length=1, max_length=500, description="The command that runs the tests")
    assumptions: tuple[str, ...] = Field(
        default=(), max_length=MAX_ASSUMPTIONS, description="What you assumed and could not check"
    )

    @field_validator("assumptions")
    @classmethod
    def _assumptions_are_sentences(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value.strip() or len(value) > MAX_TEXT_CHARS:
                raise ValueError(f"each assumption must be 1 to {MAX_TEXT_CHARS} characters")
        return values

    @model_validator(mode="after")
    def _no_file_twice(self) -> "Report":
        seen: set[str] = set()
        for change in self.files_changed_with_reason:
            if change.path in seen:
                raise ValueError(f"the file {change.path} is listed twice")
            seen.add(change.path)
        return self


class Submission:
    """Where the report of a run is kept. Each run has its own; the latest report is the one that counts."""

    def __init__(self) -> None:
        self._report: Report | None = None

    @property
    def report(self) -> Report | None:
        return self._report

    def submit(self, report: Report) -> Report | None:
        """Keep `report`. Returns the one it replaced, if there was one."""
        previous, self._report = self._report, report
        return previous
