"""SSRF-safe fetching: pinned addresses, redirects, caps and status mapping."""

import httpx
import pytest

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.infra.adapters.recipe_import.safe_fetch import (
    SafeHttpFetcher,
    is_public_address,
)

PUBLIC = "93.184.215.14"
HOSTS = {
    "example.com": [PUBLIC],
    "evil.example": ["10.0.0.5"],
    "mixed.example": [PUBLIC, "127.0.0.1"],
    "v6.example": ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
}


async def _resolver(host: str, port: int) -> list[str]:
    return HOSTS[host]


def _fetcher(handler) -> SafeHttpFetcher:
    return SafeHttpFetcher(resolver=_resolver, transport=httpx.MockTransport(handler))


async def _get(fetcher: SafeHttpFetcher, url: str, **kwargs):
    options = {
        "max_bytes": 1000,
        "accept": "text/html",
        "allowed_types": ("text/html",),
    }
    return await fetcher.get(url, **{**options, **kwargs})


def _html(body: bytes = b"<html>ok</html>", **headers) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8", **headers},
        content=body,
    )


@pytest.mark.asyncio
async def test_connects_to_the_checked_address_with_the_real_host_name():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers["host"],
                request.extensions.get("sni_hostname"),
            )
        )
        return _html()

    resource = await _get(_fetcher(handler), "https://example.com/pho")

    assert seen == [(f"https://{PUBLIC}/pho", "example.com", "example.com")]
    assert resource.url == "https://example.com/pho"
    assert resource.text() == "<html>ok</html>"


@pytest.mark.asyncio
async def test_ipv6_addresses_are_bracketed():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _html()

    await _get(_fetcher(handler), "https://v6.example/a")

    assert seen == ["https://[2606:2800:21f:cb07:6820:80da:af6b:8b2c]/a"]


@pytest.mark.parametrize("host", ["evil.example", "mixed.example"])
@pytest.mark.asyncio
async def test_refuses_hosts_that_resolve_to_private_addresses(host):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not connect")

    with pytest.raises(RecipeImportError) as caught:
        await _get(_fetcher(handler), f"https://{host}/")

    assert caught.value.failure == RecipeImportFailure.URL_NOT_ALLOWED


@pytest.mark.asyncio
async def test_every_redirect_hop_is_checked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/admin"})

    with pytest.raises(RecipeImportError) as caught:
        await _get(_fetcher(handler), "https://example.com/start")

    assert caught.value.failure == RecipeImportFailure.URL_NOT_ALLOWED


@pytest.mark.asyncio
async def test_follows_relative_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/recipe?id=1"})
        return _html()

    resource = await _get(_fetcher(handler), "https://example.com/start")

    assert resource.url == "https://example.com/recipe?id=1"


@pytest.mark.asyncio
async def test_stops_after_too_many_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(RecipeImportError) as caught:
        await _get(_fetcher(handler), "https://example.com/start")

    assert caught.value.failure == RecipeImportFailure.SOURCE_REJECTED


@pytest.mark.asyncio
async def test_large_pages_are_truncated_when_allowed_and_refused_otherwise():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html(b"x" * 5000)

    truncated = await _get(
        _fetcher(handler), "https://example.com/", truncate_oversized=True
    )
    assert (len(truncated.body), truncated.truncated) == (1000, True)

    with pytest.raises(RecipeImportError) as caught:
        await _get(_fetcher(handler), "https://example.com/")
    assert caught.value.failure == RecipeImportFailure.SOURCE_REJECTED


@pytest.mark.asyncio
async def test_declared_oversized_files_are_refused_before_reading():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html(b"x" * 10, **{"content-length": "999999"})

    with pytest.raises(RecipeImportError):
        await _get(_fetcher(handler), "https://example.com/")


@pytest.mark.asyncio
async def test_unexpected_content_types_are_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF"
        )

    with pytest.raises(RecipeImportError) as caught:
        await _get(_fetcher(handler), "https://example.com/menu.pdf")

    assert caught.value.details == {"content_type": "application/pdf"}


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        (404, RecipeImportFailure.SOURCE_REJECTED),
        (403, RecipeImportFailure.SOURCE_REJECTED),
        (429, RecipeImportFailure.SOURCE_UNREACHABLE),
        (503, RecipeImportFailure.SOURCE_UNREACHABLE),
    ],
)
@pytest.mark.asyncio
async def test_status_codes_map_to_failures(status, failure):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(RecipeImportError) as caught:
        await _get(_fetcher(handler), "https://example.com/")

    assert caught.value.failure == failure


@pytest.mark.asyncio
async def test_transport_errors_are_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(RecipeImportError) as caught:
        await _get(_fetcher(handler), "https://example.com/")

    assert caught.value.failure == RecipeImportFailure.SOURCE_UNREACHABLE


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.8.8", True),
        ("2001:4860:4860::8888", True),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("fe80::1%eth0", False),
        ("224.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_public_addresses(address, public):
    assert is_public_address(address) is public


@pytest.mark.asyncio
async def test_prefers_ipv4_and_tries_the_next_address_when_one_refuses():
    seen = []

    async def resolver(host: str, port: int) -> list[str]:
        return [
            "2606:2800:21f:cb07:6820:80da:af6b:8b2c",
            "93.184.215.14",
            "93.184.215.15",
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "93.184.215.14":
            raise httpx.ConnectError("refused", request=request)
        return _html()

    fetcher = SafeHttpFetcher(resolver=resolver, transport=httpx.MockTransport(handler))

    await _get(fetcher, "https://example.com/")

    assert seen == ["93.184.215.14", "93.184.215.15"]
