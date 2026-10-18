"""Shared HTTP infrastructure."""

from src.infra.http.client import close_shared_http_client, get_shared_http_client
from src.infra.http.image_download import (
    ScanImageDownloadAdapter,
    close_scan_image_http_client,
    download_scan_image_bytes,
    get_scan_image_http_client,
)

__all__ = [
    "ScanImageDownloadAdapter",
    "close_scan_image_http_client",
    "close_shared_http_client",
    "download_scan_image_bytes",
    "get_scan_image_http_client",
    "get_shared_http_client",
]
