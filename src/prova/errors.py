"""Prova error tree. Every error raised by our own code derives from ProvaError."""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Classification of tool errors, returned to the model as data."""

    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    INTERNAL = "internal"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    TRANSIENT = "transient"  # a failure that may go away by itself (the caller decides whether to try again)
    SAFETY = "safety"  # refused by a safety rule, not by a missing permission


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
    """A call to the model failed. Always raised with `from e`, so the SDK exception stays as `__cause__`."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code  # the HTTP status, or None for a connection error or a timeout


class StructuredErrorKind(StrEnum):
    """Why structured output was not accepted. Callers branch on this instead of parsing messages."""

    TRUNCATED = "truncated"
    REFUSED = "refused"
    UNEXPECTED_STOP = "unexpected_stop"
    NO_TEXT = "no_text"
    INVALID_JSON = "invalid_json"
    MISSING_FIELD = "missing_field"
    OUT_OF_ENUM = "out_of_enum"
    OUT_OF_RANGE = "out_of_range"
    EXTRA_FIELD = "extra_field"
    WRONG_TYPE = "wrong_type"
    OTHER = "other"


class StructuredError(ProvaError):
    """The model's output could not be turned into trusted structured data. Callers must fail closed."""

    def __init__(self, kind: StructuredErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class BudgetExceededError(ProvaError):
    """Budget exceeded (tokens, money, turns)."""
