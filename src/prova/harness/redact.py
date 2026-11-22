"""Keep secrets out of logs: replace them with a fixed marker before anything is written."""

import re
from collections.abc import Iterable, Mapping
from typing import Any, Self, cast

REDACTED = "[REDACTED]"

# Secrets with a recognisable shape. A lookbehind keeps the prefixes from matching in the middle of a word
# (`risk-ant-eater` is not a key).
_SHAPES = re.compile(
    "|".join(
        [
            # A private key block. The second form covers output that was cut before the END line.
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*",
            r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{8,}",
            r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}",
            r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}",
            r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])",
        ]
    ),
    re.DOTALL,
)

# A variable is sensitive when ONE WORD of its name says so: ANTHROPIC_API_KEY yes, KEYBOARD_LAYOUT no.
_SENSITIVE_WORDS = frozenset(
    {"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "CREDENTIALS", "PRIVATE"}
)
_MIN_VALUE_LENGTH = 8  # a shorter value (`1`, `yes`) would mangle ordinary text everywhere it appears


class Redactor:
    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        # Longest first: a value that contains another must go first, or the tail of it would be left behind.
        self._values = sorted({v for v in secret_values if v}, key=len, reverse=True)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> Self:
        """Also hide the VALUES of the sensitive variables of `environ`, whatever shape they have."""
        values = [
            value
            for name, value in environ.items()
            if len(value) >= _MIN_VALUE_LENGTH and _is_sensitive(name)
        ]
        return cls(values)

    def redact(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, REDACTED)
        return _SHAPES.sub(REDACTED, text)

    def redact_data(self, value: Any) -> Any:
        """Redact every string and dict key in nested data. Returns a copy: the input is not modified."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            pairs = cast(Mapping[Any, Any], value)
            return {self.redact_data(k): self.redact_data(v) for k, v in pairs.items()}
        if isinstance(value, list):
            return [self.redact_data(item) for item in cast(list[Any], value)]
        if isinstance(value, tuple):
            return tuple(self.redact_data(item) for item in cast(tuple[Any, ...], value))
        return value  # numbers, booleans, None: nothing to hide


def _is_sensitive(variable_name: str) -> bool:
    return bool(_SENSITIVE_WORDS & set(re.split(r"[^A-Za-z0-9]+", variable_name.upper())))
