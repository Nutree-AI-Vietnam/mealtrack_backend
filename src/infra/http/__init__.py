"""Shared HTTP infrastructure."""

from src.infra.http.client import close_shared_http_client, get_shared_http_client

__all__ = ["get_shared_http_client", "close_shared_http_client"]
