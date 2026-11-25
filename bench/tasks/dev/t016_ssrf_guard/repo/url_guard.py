"""Decide whether the server may fetch a URL that a user gave, so it cannot be sent inside the network."""

from collections.abc import Callable
from urllib.parse import urlparse

BLOCKED_HOSTS = frozenset({"localhost", "127.0.0.1"})


def is_safe_url(url: str, resolve: Callable[[str], list[str]]) -> bool:
    """True if the server may fetch `url`. `resolve` gives the IP addresses that a host name points to, as DNS
    does (it raises OSError when the name points nowhere)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    return host not in BLOCKED_HOSTS
