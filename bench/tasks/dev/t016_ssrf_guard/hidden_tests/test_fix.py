"""Behaviour of is_safe_url: a URL may be fetched only if every address its host points to is public."""

import ipaddress
from collections.abc import Callable
from types import ModuleType

import pytest

NAMES = {
    "example.com": ["93.184.216.34"],
    "ipv6.example.com": ["2606:4700::1111"],
    "internal.corp": ["10.0.0.5"],
    "half.example.com": ["93.184.216.34", "10.0.0.1"],  # one public address and one inside
    "rebind.example.com": ["127.0.0.1"],
    "localhost.": ["127.0.0.1"],
    "LOCALHOST": ["127.0.0.1"],
    # what DNS (getaddrinfo) makes of numbers written in other ways
    "2130706433": ["127.0.0.1"],
    "0x7f.0.0.1": ["127.0.0.1"],
    "0177.0.0.1": ["127.0.0.1"],
    "127.1": ["127.0.0.1"],
    "garbage.example.com": ["not an address"],
    "empty.example.com": [],
}


def resolve(host: str) -> list[str]:
    if host in NAMES:
        return NAMES[host]
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        raise OSError(f"no such host: {host}") from None


@pytest.fixture
def module(load: Callable[[str], ModuleType]) -> ModuleType:
    return load("url_guard")


@pytest.mark.parametrize(
    "host",
    [
        "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0",  # noqa: S104 - a value refused
        "100.64.0.1", "127.0.0.2",
        "224.0.0.1", "255.255.255.255",
        "[::1]", "[::]", "[fc00::1]", "[fe80::1]", "[::ffff:127.0.0.1]", "[::ffff:10.0.0.1]",
    ],
)  # fmt: skip
def test_an_address_that_is_not_public_is_refused(module: ModuleType, host: str) -> None:
    assert module.is_safe_url(f"http://{host}/", resolve) is False


@pytest.mark.parametrize(
    "host",
    [
        "internal.corp", "half.example.com", "rebind.example.com", "localhost.", "LOCALHOST",
        "2130706433", "0x7f.0.0.1", "0177.0.0.1", "127.1",
    ],
)  # fmt: skip
def test_a_name_that_points_inside_is_refused(module: ModuleType, host: str) -> None:
    assert module.is_safe_url(f"http://{host}/", resolve) is False


@pytest.mark.parametrize("host", ["nowhere.invalid", "garbage.example.com", "empty.example.com"])
def test_a_name_that_cannot_be_checked_is_refused_without_an_error(module: ModuleType, host: str) -> None:
    assert module.is_safe_url(f"http://{host}/", resolve) is False


@pytest.mark.parametrize("url", ["http:///etc/passwd", "http://", "https://:80/", "http://[::1/"])
def test_a_url_with_no_host_or_that_cannot_be_read_is_refused(module: ModuleType, url: str) -> None:
    assert module.is_safe_url(url, resolve) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/", "http://example.com:8080/a?b=c", "https://ipv6.example.com/",
        "http://8.8.8.8/", "http://[2606:4700::1111]/",
    ],
)  # fmt: skip
def test_a_public_url_may_be_fetched(module: ModuleType, url: str) -> None:
    assert module.is_safe_url(url, resolve) is True


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "gopher://example.com/", "ftp://example.com/", "javascript:alert(1)"]
)
def test_a_scheme_other_than_http_is_refused(module: ModuleType, url: str) -> None:
    assert module.is_safe_url(url, resolve) is False


def test_a_name_before_an_at_sign_does_not_count(module: ModuleType) -> None:
    assert module.is_safe_url("http://example.com@127.0.0.1/", resolve) is False
