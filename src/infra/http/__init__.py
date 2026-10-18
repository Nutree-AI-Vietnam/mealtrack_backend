"""Shared HTTP infrastructure."""

from src.infra.http.client import close_shared_http_client, get_shared_http_client
from src.infra.http.image_download import (
    ImageDownloadUnavailable,
    download_scan_image_bytes,
)

__all__ = [
    "ImageDownloadUnavailable",
    "close_shared_http_client",
    "download_scan_image_bytes",
    "get_shared_http_client",
]
