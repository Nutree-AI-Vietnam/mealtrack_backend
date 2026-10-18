"""Port for downloading a scan photo before analysis."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ImageDownloadPort(Protocol):
    """Fetch scan-photo bytes for one URL.

    The infrastructure adapter owns transport timeouts and status mapping.
    Callers receive image bytes or the adapter's existing download errors.
    """

    async def __call__(self, image_url: str) -> bytes:
        """Return the response body for ``image_url``."""
