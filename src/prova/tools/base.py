"""What a tool is, what it hands back to the agent loop, and how the model sees that result."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, Self

from anthropic.types import ToolResultBlockParam
from pydantic import BaseModel

from prova.errors import ErrorCode
from prova.harness.patterns import PathMatcher
from prova.harness.permissions import DEFAULT_DENY_PATHS, Permission, Profile
from prova.harness.report import Submission
from prova.harness.snapshots import SnapshotStore
from prova.sandbox.base import Sandbox


@dataclass(frozen=True, slots=True)
class RunContext:
    """What a tool needs to know about the run it is part of."""

    workspace: Path  # the one directory file tools may touch
    strict: bool = False  # tests set this so a bug inside a tool is raised, not reported to the model
    # What this run may do. Enforced when a tool is called, not only when tools are listed for the model.
    permissions: frozenset[Permission] = frozenset({Permission.READ})
    # Paths no tool may read, search or list, even though they are inside the workspace (secrets).
    deny: PathMatcher = field(default_factory=lambda: PathMatcher(DEFAULT_DENY_PATHS))
    # Where write tools may write. The default matches nothing: without write paths a run cannot write.
    write: PathMatcher = field(default_factory=lambda: PathMatcher(()))
    # Paths a run may read but never write, even where `write` allows it. Checked first, so it always wins.
    write_deny: PathMatcher = field(default_factory=lambda: PathMatcher(()))
    # The originals of the files this run changed, so the changes can be undone. One store per run.
    snapshots: SnapshotStore = field(default_factory=SnapshotStore)
    # The final report of the run, once the agent hands it in (see the submit_result tool). One per run.
    submission: Submission = field(default_factory=Submission)
    # Where commands run. None means this run has no sandbox, and then no command runs at all.
    sandbox: Sandbox | None = None

    @classmethod
    def from_profile(
        cls, profile: Profile, workspace: Path, *, strict: bool = False, sandbox: Sandbox | None = None
    ) -> Self:
        """A context for a run in `workspace` under `profile`: its permissions, deny-list and write paths.
        Without a `sandbox`, no command can run, whatever the profile allows."""
        return cls(
            workspace=workspace,
            strict=strict,
            permissions=profile.allow,
            deny=PathMatcher(profile.denied_paths),
            write=PathMatcher(profile.write_paths),
            write_deny=PathMatcher(profile.write_deny_paths),
            sandbox=sandbox,
        )


@dataclass(frozen=True, slots=True)
class ToolResult:
    success: bool
    message: str  # one short sentence for the model: what happened
    data: Any = None  # the full payload for code and logs; never sent to the model
    snapshot: str | None = None  # what the model sees after the message; if it is cut, it must say so
    error_code: ErrorCode | None = None
    notes: tuple[str, ...] = ()  # added by hooks after the tool ran: information, never a change of outcome

    def __post_init__(self) -> None:
        if self.success and self.error_code is not None:
            raise ValueError("a successful ToolResult cannot carry an error_code")
        if not self.success and self.error_code is None:
            raise ValueError("a failed ToolResult needs an error_code")

    @classmethod
    def ok(cls, message: str, *, data: Any = None, snapshot: str | None = None) -> Self:
        return cls(success=True, message=message, data=data, snapshot=snapshot)

    @classmethod
    def error(cls, code: ErrorCode, message: str) -> Self:
        return cls(success=False, message=message, error_code=code)

    def with_notes(self, *notes: str) -> Self:
        """A copy with `notes` added at the end. The outcome (success, data, error code) is not touched."""
        return replace(self, notes=(*self.notes, *notes)) if notes else self

    def model_text(self) -> str:
        """Exactly the text the model is shown: the message, the snapshot and the notes, never `data`."""
        if not self.success:
            text = f"{self.error_code}: {self.message}"
        else:
            text = self.message if self.snapshot is None else f"{self.message}\n\n{self.snapshot}"
        return f"{text}\n\n" + "\n".join(self.notes) if self.notes else text

    def to_tool_result_block(
        self, tool_use_id: str, *, redact: Callable[[str], str] | None = None
    ) -> ToolResultBlockParam:
        """The `tool_result` block for the API. `redact` cleans secrets out of the text first."""
        text = self.model_text()
        block: ToolResultBlockParam = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": redact(text) if redact is not None else text,
        }
        if not self.success:
            block["is_error"] = True
        return block


class Tool(Protocol):
    """Anything with this shape is a tool; there is no base class to inherit from."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def args_model(self) -> type[BaseModel]: ...  # the JSON schema sent to the API is generated from this

    @property
    def permission(self) -> Permission: ...

    def run(self, args: Any, ctx: RunContext) -> ToolResult: ...  # args is an instance of `args_model`
