import ipaddress

import pytest
from url_guard import is_safe_url

NAMES = {"example.com": ["93.184.216.34"], "internal.corp": ["10.0.0.5"]}


def resolve(host: str) -> list[str]:
    """Like DNS: a name from the table, or an address written as an address."""
    if host in NAMES:
        return NAMES[host]
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        raise OSError(f"no such host: {host}") from None


def test_a_public_site_may_be_fetched() -> None:
    assert is_safe_url("https://example.com/page", resolve) is True


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/", "http://localhost/admin"])
def test_other_schemes_and_the_machine_itself_are_refused(url: str) -> None:
    assert is_safe_url(url, resolve) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://10.1.2.3/",
        "http://169.254.169.254/latest/meta-data/",  # the cloud's metadata service
        "http://[::1]:8080/",
        "http://internal.corp/",  # a name that points inside
        "http://nowhere.invalid/",  # a name that points nowhere
    ],
)
def test_an_address_inside_the_network_is_refused(url: str) -> None:
    assert is_safe_url(url, resolve) is False
