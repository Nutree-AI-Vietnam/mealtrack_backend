"""Shared connection-pooled HTTP client provider."""

from __future__ import annotations

import httpx

_shared_client: httpx.AsyncClient | None = None


def get_shared_http_client() -> httpx.AsyncClient:
    """Return a singleton, connection-pooled httpx.AsyncClient.

    Configured with keepalive limits and connection pooling to avoid
    per-request TCP/TLS handshake overhead.
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=30,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(
                connect=5.0,
                read=30.0,
                write=10.0,
                pool=5.0,
            ),
        )
    return _shared_client


async def close_shared_http_client() -> None:
    """Gracefully close the singleton HTTP client."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None
