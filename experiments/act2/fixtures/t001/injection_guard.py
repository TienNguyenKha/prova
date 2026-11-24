"""A small guard that flags text trying to override an assistant's instructions."""

import re

# Phrases like "ignore instructions", "ignore all instructions", "ignore your instructions".
_OVERRIDE = re.compile(r"ignore (all |your |previous )?instructions", re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    """True when `text` asks the reader to ignore its instructions."""
    return _OVERRIDE.search(text) is not None
