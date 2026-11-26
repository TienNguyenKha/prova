"""The mechanisms a rule of the contract can stand on, and for each one the probe that shows it works.

A probe does not read a configuration and reason about it. It does what the agent would do, with the code
the agent would meet: it tries to write a file with the real tool, it builds the real `docker run` line, it
feeds the real redactor something that looks like a key. A mechanism returns None when it holds, and a
sentence saying how it does not when it fails.
"""

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from prova.contract import OUTPUT_FIELDS, Mechanism, VerifyContext
from prova.errors import ErrorCode
from prova.harness.hooks import hook_name
from prova.harness.permissions import Permission, Profile
from prova.harness.redact import Redactor
from prova.registry import Registry
from prova.sandbox.docker import DockerSandbox
from prova.tools.base import RunContext, Tool, ToolResult
from prova.tools.edit import CreateFileTool
from prova.tools.fs import ReadFileTool
from prova.tools.registry import ToolRegistry

# Hooks the contract may name: (the list they must be in, their name).
_KNOWN_HOOKS: tuple[tuple[Literal["pre", "post", "stop"], str], ...] = (
    ("post", "lint_after_edit"),
    ("stop", "files_listed"),
    ("stop", "tests_before_stop"),
    ("stop", "repro_fails_before_stop"),
)
_FAKE_KEY = "sk-ant-api03-AbC123_xyz-9876-QwErTy"  # the shape of a key, not a real one
_FAKE_PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----"
)


def builtin_mechanisms() -> dict[str, Mechanism]:
    """The mechanisms Prova ships. A new table each time: nobody can change what the next caller gets."""
    table: dict[str, Mechanism] = {
        "permissions.write_blocked": _write_blocked,
        "permissions.read_blocked": _read_blocked,
        "permissions.lacks": _lacks,
        "sandbox.network_none": _network_none,
        "trajectory.redaction": _redaction,
        "tool.submit_result": _submit_result,
    }
    for kind, name in _KNOWN_HOOKS:
        table[f"hook.{kind}.{name}"] = _hook_installed(kind, name)
    return table


# -- permissions ---------------------------------------------------------------------------------------


def _write_blocked(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
    """Params: `profile`, `path`. Holds when the profile cannot write `path` with the real write tool."""
    found = _profile_and_path(ctx, params)
    if isinstance(found, str):
        return found
    name, profile, path = found
    with tempfile.TemporaryDirectory() as workspace:
        run_ctx = RunContext.from_profile(profile, Path(workspace))
        result = _probe_tools().dispatch("create_file", {"path": path, "content": "probe\n"}, run_ctx)
    return _blocked_or_problem(result, f"the profile {name!r} lets {path} be written")


def _read_blocked(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
    """Params: `profile`, `path`. Holds when the profile cannot read `path` with the real read tool."""
    found = _profile_and_path(ctx, params)
    if isinstance(found, str):
        return found
    name, profile, path = found
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            return f"the path {path!r} must be relative and inside the workspace"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("probe\n", encoding="utf-8")  # it must exist: "not found" is not "blocked"
        result = _probe_tools().dispatch("read_file", {"path": path}, RunContext.from_profile(profile, root))
    return _blocked_or_problem(result, f"the profile {name!r} lets {path} be read")


def _lacks(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
    """Params: `profile`, `permission`. Holds when the profile does not have that permission."""
    values = _strings(params, "profile", "permission")
    if isinstance(values, str):
        return values
    profile = _profile(ctx, values["profile"])
    if isinstance(profile, str):
        return profile
    try:
        permission = Permission(values["permission"])
    except ValueError:
        return f"{values['permission']!r} is not a permission"
    if permission in profile.allow:
        return f"the profile {values['profile']!r} has the {permission.value} permission"
    return None


# -- sandbox and log -----------------------------------------------------------------------------------


def _network_none(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
    """Holds when the `docker run` line of the sandbox has `--network none` and no other network setting."""
    command = DockerSandbox(ctx.project_root).build_command("probe", ["true"], "/work")
    settings = [command[i + 1] for i in range(len(command) - 1) if command[i] == "--network"]
    if settings != ["none"]:
        found = settings or "no network setting"
        return f"the sandbox command does not carry exactly one --network none (it has {found})"
    return None


def _redaction(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
    """Holds when the redactor removes something shaped like an API key and a private key block."""
    cleaned = Redactor().redact(f"token {_FAKE_KEY} and then {_FAKE_PRIVATE_KEY}")
    if _FAKE_KEY in cleaned:
        return "the redactor left an API-key-shaped text in place"
    if "PRIVATE KEY" in cleaned:
        return "the redactor left a private key block in place"
    return None


# -- the report -----------------------------------------------------------------------------------------


def _submit_result(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
    """Optional param: `profile`. Holds when the agent can hand in its report: the tool is registered, has the
    fields of the output contract, is offered to the profile, and records a report the way it is called."""
    name = "submit_result"
    registered = {tool["name"]: tool for tool in ctx.tools.to_api_tools(set(Permission))}
    tool = registered.get(name)
    if tool is None:
        return f"the tool {name!r} is not registered"
    schema: dict[str, Any] = dict(tool["input_schema"])  # the schema the model is given
    properties: Mapping[str, Any] = schema.get("properties") or {}
    missing = [field for field in OUTPUT_FIELDS if field not in properties]
    if missing:
        return f"the tool {name!r} lacks the fields of the output contract: {', '.join(missing)}"

    permissions = frozenset(Permission)
    profile_name = params.get("profile")
    profile: Profile | None = None
    if profile_name is not None:
        found = _profile(ctx, str(profile_name))
        if isinstance(found, str):
            return found
        profile, permissions = found, found.allow
        if name not in {offered["name"] for offered in ctx.tools.to_api_tools(permissions)}:
            return f"the tool {name!r} is not offered to the profile {profile_name!r}"

    with tempfile.TemporaryDirectory() as workspace:
        run_ctx = (
            RunContext.from_profile(profile, Path(workspace))
            if profile is not None
            else RunContext(workspace=Path(workspace), permissions=permissions)
        )
        sample: dict[str, Any] = {"files_changed_with_reason": [], "test_command": "probe"}
        result = ctx.tools.dispatch(name, sample, run_ctx)
    if not result.success:
        return f"the probe report was refused: {result.message}"
    if run_ctx.submission.report is None:
        return f"the tool {name!r} took the report but did not record it"
    return None


# -- hooks ---------------------------------------------------------------------------------------------


def _hook_installed(kind: Literal["pre", "post", "stop"], name: str) -> Mechanism:
    def check(ctx: VerifyContext, params: Mapping[str, Any]) -> str | None:
        installed = getattr(ctx.hooks, kind)
        if any(hook_name(hook) == name for hook in installed):
            return None
        return f"the hook {name!r} is not installed in the {kind} hooks of this run"

    return check


# -- helpers -------------------------------------------------------------------------------------------


def _strings(params: Mapping[str, Any], *names: str) -> dict[str, str] | str:
    values: dict[str, str] = {}
    for name in names:
        value = params.get(name)
        if not isinstance(value, str) or not value:
            return f"needs the parameter {name!r} (a non-empty string)"
        values[name] = value
    return values


def _profile(ctx: VerifyContext, name: str) -> Profile | str:
    profile = ctx.profiles.get(name)
    return profile if profile is not None else f"there is no profile {name!r}"


def _profile_and_path(ctx: VerifyContext, params: Mapping[str, Any]) -> tuple[str, Profile, str] | str:
    values = _strings(params, "profile", "path")
    if isinstance(values, str):
        return values
    profile = _profile(ctx, values["profile"])
    if isinstance(profile, str):
        return profile
    return values["profile"], profile, values["path"]


def _probe_tools() -> ToolRegistry:
    registry = Registry[Tool]("tool")
    registry.register(CreateFileTool())
    registry.register(ReadFileTool())
    registry.freeze()
    return ToolRegistry(registry)


def _blocked_or_problem(result: ToolResult, if_allowed: str) -> str | None:
    """A refusal by the permissions is what we want. Anything else, even another error, proves nothing."""
    if result.error_code == ErrorCode.PERMISSION_DENIED:
        return None
    if result.success:
        return if_allowed
    return f"the probe could not tell: {result.message}"
