import re

INJECTION_PATTERNS = [
    r"ignore (all |your |previous )?instructions",
    r"you are now",
    r"pretend (you are|to be)",
    r"system prompt",
    r"forget everything",
    r"new instructions:",
    r"override",
    r"DAN mode",
    r"do anything now",
    r"jailbreak",
]


def check_injection_patterns(text: str) -> dict:
    """Layer 1: Fast regex-based injection detection (~1ms, free)."""
    for pattern in INJECTION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return {"blocked": True, "reason": f"Pattern match: '{match.group()}'", "layer": 1}
    return {"blocked": False, "layer": 1}
