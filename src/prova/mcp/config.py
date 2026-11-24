"""mcp.yaml: which MCP servers a run may start, and what each of them is allowed to do.

An MCP server is a program somebody wrote, started as a child process. Nothing is started unless it is
listed here, and each server has its own permission: a tool of that server is only usable in a run that
has it.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from prova.errors import ConfigError
from prova.harness.permissions import Permission
from prova.validation import describe_errors

DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0
# The name goes into the tool name `mcp__<server>__<tool>`: only what cannot break that format, and no `__`.
_SERVER_NAME = re.compile(r"[a-z][a-z0-9_]{0,23}")


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class McpServerConfig:
    command: tuple[str, ...]  # the program and its arguments: a list, never a line for a shell
    permission: Permission  # required: there is no default permission
    timeout_s: float = Field(default=DEFAULT_TIMEOUT_S, gt=0, le=MAX_TIMEOUT_S)  # for one call of one tool

    @field_validator("command", mode="before")
    @classmethod
    def _command_is_a_list_of_arguments(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            raise ValueError("command must be a list of arguments (there is no shell to read a command line)")
        items: list[object] = list(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        if not items or not all(isinstance(item, str) and item for item in items):
            raise ValueError("command must be a non-empty list of non-empty strings")
        return tuple(items)


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servers: dict[str, McpServerConfig]

    @field_validator("servers")
    @classmethod
    def _names_are_safe(cls, servers: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
        for name in servers:
            if not _SERVER_NAME.fullmatch(name) or "__" in name:
                raise ValueError(
                    f"server name {name!r} is not allowed: use lowercase letters, digits and single "
                    "underscores, start with a letter, at most 24 characters"
                )
        return servers


@dataclass(frozen=True, slots=True)
class McpConfig:
    servers: Mapping[str, McpServerConfig]
    base_dir: Path  # where the file is: the servers are started from here


def load_mcp_config(path: Path) -> McpConfig:
    """Read mcp.yaml. Anything wrong with it is a ConfigError that names the file and the problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read the MCP config {path}: {e.strerror or e}") from e
    try:
        raw: Any = yaml.safe_load(text)  # safe_load: a YAML file must never construct objects or run code
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML ({type(e).__name__}); nothing was loaded") from e
    if not isinstance(raw, dict) or "servers" not in raw:
        raise ConfigError(f"{path} must be a mapping with a 'servers' key")
    try:
        parsed = _ConfigFile.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path} has an invalid MCP server: {describe_errors(e)}") from e
    return McpConfig(servers=MappingProxyType(dict(parsed.servers)), base_dir=path.parent.resolve())
