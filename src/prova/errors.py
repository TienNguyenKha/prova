"""Prova error tree. Every error raised by our own code derives from ProvaError."""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Classification of tool errors, returned to the model as data."""

    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    INTERNAL = "internal"
    # TODO(M4): add more codes when the real tools are written (timeout, permission denied, ...)


class ProvaError(Exception):
    """Root of every error raised deliberately by Prova."""


class ConfigError(ProvaError):
    """Missing or invalid configuration. Raised at load_settings time."""


class RegistryError(ProvaError):
    """Base of the errors caused by misusing a Registry."""


class DuplicateNameError(RegistryError):
    """Two items registered under the same name."""


class UnknownNameError(RegistryError):
    """get() called with a name that was never registered."""


class InvalidNameError(RegistryError):
    """Name does not match the allowed format."""


class RegistryFrozenError(RegistryError):
    """Registration attempted after the registry was frozen."""


class ToolError(ProvaError):
    """A tool reports a classified failure. The tool boundary turns it into a ToolResult."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class SandboxError(ProvaError):
    """Error coming from the sandbox."""


class LLMError(ProvaError):
    """Error from the model-calling layer. Use `raise LLMError(...) from e` to keep the SDK exception."""


class BudgetExceededError(ProvaError):
    """Budget exceeded (tokens, money, turns)."""
