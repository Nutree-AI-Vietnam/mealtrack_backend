"""Check whether a stored image URL already serves a photo."""

from __future__ import annotations

import httpx


async def image_url_exists(url: str, *, timeout: float = 3.0) -> bool:
    """Return True when a HEAD of the delivery URL succeeds."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.head(url)
    except Exception:
        return False
    return response.status_code == 200
