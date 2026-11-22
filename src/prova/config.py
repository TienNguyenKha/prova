"""Prova configuration. Only load_settings() reads the environment, once, at program start."""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from prova.errors import ConfigError

ENV_PREFIX = "PROVA_"
API_KEY_VAR = "ANTHROPIC_API_KEY"
# This file lives at <root>/src/prova/config.py, so <root> is parents[2]. Independent of the cwd.
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    anthropic_api_key: SecretStr
    model: str = "claude-sonnet-5-5"
    cheap_model: str = "claude-haiku-5-5"
    max_tokens: int = Field(4096, gt=0)
    request_timeout_s: float = Field(120.0, gt=0)
    project_root: Path


def load_settings(
    env_file: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    **overrides: object,
) -> Settings:
    """Merge defaults < .env < environ < overrides; raise ConfigError naming the bad variable."""
    env_path = DEFAULT_ENV_FILE if env_file is None else env_file
    file_values = dotenv_values(env_path)  # a missing file yields an empty dict
    live_environ = os.environ if environ is None else environ

    fields: dict[str, object] = {}
    for source in (file_values, live_environ):  # later sources override earlier ones
        for name, value in source.items():
            if value is not None and _is_ours(name):
                fields[_field_name(name)] = value

    fields.update(overrides)
    fields.setdefault("project_root", env_path.parent)

    try:
        return Settings.model_validate(fields)
    except ValidationError as e:
        raise ConfigError(_describe(e)) from e


def _is_ours(env_name: str) -> bool:
    """Only Prova's variables enter Settings; PATH, HOME, ... are ignored."""
    return env_name == API_KEY_VAR or env_name.startswith(ENV_PREFIX)


def _field_name(env_name: str) -> str:
    if env_name == API_KEY_VAR:
        return "anthropic_api_key"
    return env_name.removeprefix(ENV_PREFIX).lower()


def _env_name(field: str) -> str:
    if field == "anthropic_api_key":
        return API_KEY_VAR
    return ENV_PREFIX + field.upper()


def _describe(error: ValidationError) -> str:
    lines: list[str] = []
    for item in error.errors():
        name = _env_name(str(item["loc"][0])) if item["loc"] else "?"
        if item["type"] == "missing":
            lines.append(f"- {name}: missing. Set it in .env (see .env.example) or the environment.")
        elif item["type"] == "extra_forbidden":
            lines.append(f"- {name}: unknown variable (typo in the name?)")
        else:
            lines.append(f"- {name}: {item['msg']}")
    return "Invalid configuration:\n" + "\n".join(lines)
