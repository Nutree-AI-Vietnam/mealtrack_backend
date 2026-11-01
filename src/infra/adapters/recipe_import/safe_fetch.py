"""Fetch user-supplied links without exposing private networks (SSRF).

Every hop, redirects included, is validated, resolved, and refused when any
address is not public. The connection is then pinned to the checked address
(TLS still verifies the real host name through SNI), so a DNS answer that
changes between the check and the connect cannot reach an internal service.
Each fetch uses its own client, so a pinned connection is never reused for a
different host.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import.link_policy import normalize_recipe_url

Resolver = Callable[[str, int], Awaitable[list[str]]]

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36 "
    "NutreeRecipeImport/1.0"
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TRANSIENT_STATUSES = frozenset({408, 425, 429})


@dataclass(frozen=True, slots=True)
class FetchedResource:
    url: str
    status_code: int
    content_type: str
    body: bytes
    charset: str | None
    truncated: bool = False

    def text(self) -> str:
        try:
            return self.body.decode(self.charset or "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


async def resolve_host(host: str, port: int) -> list[str]:
    """All addresses ``host`` resolves to, as strings."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5)):
            raise RecipeImportError(
                RecipeImportFailure.SOURCE_REJECTED, "This website could not be found."
            ) from exc
        raise RecipeImportError(
            RecipeImportFailure.SOURCE_UNREACHABLE, "The website could not be reached."
        ) from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class SafeHttpFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = resolve_host,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
        max_redirects: int = 5,
        user_agent: str = BROWSER_USER_AGENT,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._timeout = timeout_seconds
        self._max_redirects = max_redirects
        self._user_agent = user_agent

    async def get(
        self,
        url: str,
        *,
        max_bytes: int,
        accept: str,
        allowed_types: tuple[str, ...],
        truncate_oversized: bool = False,
    ) -> FetchedResource:
        """GET ``url``; raise ``RecipeImportError`` for anything unusable."""
        try:
            async with asyncio.timeout(self._timeout):
                return await self._follow(
                    url,
                    max_bytes=max_bytes,
                    accept=accept,
                    allowed_types=allowed_types,
                    truncate_oversized=truncate_oversized,
                )
        except TimeoutError as exc:
            raise RecipeImportError(
                RecipeImportFailure.SOURCE_UNREACHABLE, "The website took too long."
            ) from exc
        except httpx.HTTPError as exc:
            raise RecipeImportError(
                RecipeImportFailure.SOURCE_UNREACHABLE,
                "The website could not be reached.",
                details={"error": type(exc).__name__},
            ) from exc

    async def _follow(
        self,
        url: str,
        *,
        max_bytes: int,
        accept: str,
        allowed_types: tuple[str, ...],
        truncate_oversized: bool,
    ) -> FetchedResource:
        current = normalize_recipe_url(url)
        async with httpx.AsyncClient(
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
            timeout=self._timeout,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            for _ in range(self._max_redirects + 1):
                response = await self._send_pinned(client, current, accept)
                try:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise _rejected("The website sent an empty redirect.")
                        current = normalize_recipe_url(urljoin(current, location))
                        continue
                    _raise_for_status(response.status_code)
                    content_type = response.headers.get("content-type", "")
                    media_type = content_type.split(";", 1)[0].strip().lower()
                    if not any(media_type.startswith(t) for t in allowed_types):
                        raise _rejected(
                            "The link does not point to a supported page.",
                            content_type=media_type or "unknown",
                        )
                    body, truncated = await _read_capped(
                        response, max_bytes, truncate_oversized
                    )
                    return FetchedResource(
                        url=current,
                        status_code=response.status_code,
                        content_type=media_type,
                        body=body,
                        charset=response.charset_encoding,
                        truncated=truncated,
                    )
                finally:
                    await response.aclose()
        raise _rejected("The website redirected too many times.")

    async def _send_pinned(
        self, client: httpx.AsyncClient, url: str, accept: str
    ) -> httpx.Response:
        """Send to the first checked address that accepts a connection."""
        addresses = await self._checked_addresses(url)
        for index, address in enumerate(addresses):
            request = self._pinned_request(client, url, accept, address)
            try:
                return await client.send(request, stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # IPv6 may be unroutable from the server; try the next one.
                if index == len(addresses) - 1:
                    raise
        raise AssertionError("unreachable")

    async def _checked_addresses(self, url: str) -> list[str]:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = await self._resolver(host, port)
        if not addresses:
            raise RecipeImportError(
                RecipeImportFailure.SOURCE_UNREACHABLE,
                "The website could not be reached.",
            )
        if not all(is_public_address(address) for address in addresses):
            raise RecipeImportError(
                RecipeImportFailure.URL_NOT_ALLOWED,
                "This link points to a private or local address.",
            )
        # IPv4 first, at most three tries.
        return sorted(addresses, key=lambda address: ":" in address)[:3]

    def _pinned_request(
        self, client: httpx.AsyncClient, url: str, accept: str, address: str
    ) -> httpx.Request:
        parts = urlsplit(url)
        host = parts.hostname or ""
        literal = f"[{address}]" if ":" in address else address
        netloc = literal if parts.port is None else f"{literal}:{parts.port}"
        pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
        extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
        return client.build_request(
            "GET",
            pinned,
            headers={
                "Host": parts.netloc,
                "User-Agent": self._user_agent,
                "Accept": accept,
                "Accept-Language": "en-US,en;q=0.8,vi;q=0.6",
            },
            extensions=extensions,
        )


async def _read_capped(
    response: httpx.Response, max_bytes: int, truncate: bool
) -> tuple[bytes, bool]:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes and not truncate:
        raise _rejected("The file is too large to import.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            if not truncate:
                raise _rejected("The file is too large to import.")
            chunks.append(chunk[: max(0, len(chunk) - (size - max_bytes))])
            return b"".join(chunks), True
        chunks.append(chunk)
    return b"".join(chunks), False


def _raise_for_status(status_code: int) -> None:
    if 200 <= status_code < 300:
        return
    if status_code >= 500 or status_code in _TRANSIENT_STATUSES:
        raise RecipeImportError(
            RecipeImportFailure.SOURCE_UNREACHABLE,
            "The website is not responding right now.",
            details={"status": status_code},
        )
    raise _rejected(
        "The website refused the request or the page does not exist.",
        status=status_code,
    )


def _rejected(message: str, **details: object) -> RecipeImportError:
    return RecipeImportError(
        RecipeImportFailure.SOURCE_REJECTED, message, details=dict(details) or None
    )
