"""Tests for prova.config: precedence, helpful errors, no key leaks, os.environ untouched."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from prova.config import Settings, load_settings
from prova.errors import ConfigError

KEY = "sk-ant-test-0123456789"


def write_env(path: Path, **values: str) -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


# --- Missing / invalid configuration ----------------------------------------


def test_missing_key_raises_config_error_naming_the_variable(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_settings(tmp_path / ".env", environ={})


def test_negative_max_tokens_raises_config_error(tmp_path: Path) -> None:
    environ = {"ANTHROPIC_API_KEY": KEY, "PROVA_MAX_TOKENS": "-1"}
    with pytest.raises(ConfigError, match="PROVA_MAX_TOKENS"):
        load_settings(tmp_path / ".env", environ=environ)


def test_misspelled_prova_variable_raises_config_error(tmp_path: Path) -> None:
    environ = {"ANTHROPIC_API_KEY": KEY, "PROVA_MODLE": "x"}
    with pytest.raises(ConfigError, match="PROVA_MODLE"):
        load_settings(tmp_path / ".env", environ=environ)


def test_non_prova_variables_are_ignored(tmp_path: Path) -> None:
    # extra="forbid" applies to PROVA_* variables only, not the whole environment (PATH, HOME, ...)
    environ = {"ANTHROPIC_API_KEY": KEY, "PATH": "/usr/bin", "HOME": "/home/x"}
    settings = load_settings(tmp_path / ".env", environ=environ)
    assert settings.model == "claude-sonnet-5-5"


# --- Precedence: override > environ > .env > default ------------------------


def test_precedence_override_beats_environ_beats_dotenv_beats_default(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", ANTHROPIC_API_KEY=KEY, PROVA_MODEL="a")
    environ = {"PROVA_MODEL": "b"}

    assert load_settings(env_file, environ=environ, model="c").model == "c"
    assert load_settings(env_file, environ=environ).model == "b"
    assert load_settings(env_file, environ={}).model == "a"


def test_default_model_when_nothing_is_set(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / ".env", environ={"ANTHROPIC_API_KEY": KEY})
    assert settings.model == "claude-sonnet-5-5"


def test_key_can_come_from_dotenv_file(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", ANTHROPIC_API_KEY=KEY)
    settings = load_settings(env_file, environ={})
    assert settings.anthropic_api_key.get_secret_value() == KEY


def test_missing_dotenv_file_is_not_an_error_if_environ_has_key(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "does-not-exist.env", environ={"ANTHROPIC_API_KEY": KEY})
    assert settings.anthropic_api_key.get_secret_value() == KEY


# --- No key leaks, no global state changes ----------------------------------


def test_repr_and_str_do_not_contain_the_key(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / ".env", environ={"ANTHROPIC_API_KEY": KEY})
    assert KEY not in repr(settings)
    assert KEY not in str(settings)


def test_load_settings_does_not_modify_os_environ(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", ANTHROPIC_API_KEY=KEY, PROVA_MODEL="a")
    before = dict(os.environ)
    load_settings(env_file, environ={})
    assert dict(os.environ) == before


def test_settings_is_frozen(tmp_path: Path) -> None:
    settings: Settings = load_settings(tmp_path / ".env", environ={"ANTHROPIC_API_KEY": KEY})
    with pytest.raises(ValidationError):
        settings.model = "other"  # pyright: ignore[reportAttributeAccessIssue]
