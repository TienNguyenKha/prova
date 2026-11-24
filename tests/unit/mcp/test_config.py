"""Tests for prova.mcp.config: which MCP servers a run may start, and what each may do."""

import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from prova.errors import ConfigError
from prova.harness.permissions import Permission
from prova.mcp.config import McpConfig, McpServerConfig, load_mcp_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "mcp.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


VALID = """
    servers:
      knowledge:
        command: ["python", "fixtures/server.py"]
        permission: read
        timeout_s: 10
"""


# --- A good file ---------------------------------------------------------------


def test_a_valid_file_gives_one_config_per_server(tmp_path: Path) -> None:
    config = load_mcp_config(write(tmp_path, VALID))
    assert list(config.servers) == ["knowledge"]
    server = config.servers["knowledge"]
    assert server.command == ("python", "fixtures/server.py")
    assert server.permission == Permission.READ
    assert server.timeout_s == 10


def test_the_timeout_has_a_default(tmp_path: Path) -> None:
    text = """
        servers:
          a:
            command: ["python", "s.py"]
            permission: read
    """
    assert load_mcp_config(write(tmp_path, text)).servers["a"].timeout_s == 30


def test_the_servers_run_from_the_directory_of_the_file(tmp_path: Path) -> None:
    assert load_mcp_config(write(tmp_path, VALID)).base_dir == tmp_path.resolve()


def test_a_file_with_no_servers_is_fine_it_just_has_none(tmp_path: Path) -> None:
    assert load_mcp_config(write(tmp_path, "servers: {}\n")).servers == {}


def test_the_config_cannot_be_changed_after_loading(tmp_path: Path) -> None:
    config = load_mcp_config(write(tmp_path, VALID))
    assert isinstance(config, McpConfig)
    with pytest.raises(FrozenInstanceError):
        config.base_dir = tmp_path  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        config.servers["knowledge"].timeout_s = 99  # type: ignore[misc]
    assert isinstance(config.servers["knowledge"], McpServerConfig)


# --- Nothing is allowed unless it is written down --------------------------------


def test_the_permission_is_required_and_the_error_names_the_server(tmp_path: Path) -> None:
    text = """
        servers:
          knowledge:
            command: ["python", "s.py"]
    """
    with pytest.raises(ConfigError, match=r"knowledge.*permission"):
        load_mcp_config(write(tmp_path, text))


def test_an_unknown_permission_is_refused(tmp_path: Path) -> None:
    text = VALID.replace("permission: read", "permission: admin")
    with pytest.raises(ConfigError, match="permission"):
        load_mcp_config(write(tmp_path, text))


def test_a_misspelled_key_is_an_error_not_a_silent_default(tmp_path: Path) -> None:
    text = VALID.replace("permission: read", "permision: read")
    with pytest.raises(ConfigError, match="permision"):
        load_mcp_config(write(tmp_path, text))


# --- The command is a list of arguments, never a line for a shell ---------------


def test_a_command_written_as_one_string_is_refused_because_there_is_no_shell(tmp_path: Path) -> None:
    text = VALID.replace('["python", "fixtures/server.py"]', '"python fixtures/server.py && rm -rf ~"')
    with pytest.raises(ConfigError, match="list"):
        load_mcp_config(write(tmp_path, text))


@pytest.mark.parametrize("command", ["[]", '["python", ""]', '["python", 3]', "[null]"])
def test_an_empty_or_non_text_command_is_refused(tmp_path: Path, command: str) -> None:
    text = VALID.replace('["python", "fixtures/server.py"]', command)
    with pytest.raises(ConfigError, match="command"):
        load_mcp_config(write(tmp_path, text))


# --- Names: the name ends up inside mcp__<server>__<tool> -------------------------


@pytest.mark.parametrize("name", ["Knowledge", "a__b", "has space", "-lead", "x" * 25, "1abc", ""])
def test_a_server_name_that_could_break_the_tool_name_is_refused(tmp_path: Path, name: str) -> None:
    text = VALID.replace("knowledge:", f"{name!r}:" if name else '"":')
    with pytest.raises(ConfigError, match="server name"):
        load_mcp_config(write(tmp_path, text))


# --- Limits ---------------------------------------------------------------------


@pytest.mark.parametrize("timeout", ["0", "-5", "301", "'ten'"])
def test_a_timeout_outside_the_allowed_range_is_refused(tmp_path: Path, timeout: str) -> None:
    text = VALID.replace("timeout_s: 10", f"timeout_s: {timeout}")
    with pytest.raises(ConfigError, match="timeout"):
        load_mcp_config(write(tmp_path, text))


# --- A file that cannot be trusted -----------------------------------------------


def test_a_missing_file_says_which_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="mcp.yaml"):
        load_mcp_config(tmp_path / "mcp.yaml")


def test_a_file_that_is_not_yaml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_mcp_config(write(tmp_path, "servers: [unclosed"))


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "servers: [1, 2]\n", "other: {}\n"])
def test_a_file_of_the_wrong_shape_is_a_config_error(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        load_mcp_config(write(tmp_path, text))


def test_yaml_cannot_run_code_while_it_is_loaded(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    text = f"servers: !!python/object/apply:pathlib.Path.touch [{marker.as_posix()!r}]\n"
    with pytest.raises(ConfigError):
        load_mcp_config(write(tmp_path, text))
    assert not marker.exists()
