"""Cloudflare Images adapter for catalog meal art.

User meal uploads stay on Cloudinary. This store only persists generated
catalog bytes and returns a public imagedelivery.net URL.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from src.domain.ports.image_store_port import ImageStorePort

_UPLOAD_PATH = "/images/v1"
_CATALOG_ONLY = (
    "CloudflareImagesStore is catalog-only; user meal uploads still use Cloudinary"
)


class CloudflareImagesStore(ImageStorePort):
    """Upload catalog images to Cloudflare Images and return a public variant URL."""

    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        variant: str = "public",
        timeout: float = 60,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._account_id = account_id.strip()
        self._api_token = api_token.strip()
        self._variant = (variant or "public").strip() or "public"
        self._timeout = timeout
        self._transport = transport
        if not self._account_id:
            raise ValueError("CLOUDFLARE_ACCOUNT_ID is required")
        if not self._api_token:
            raise ValueError(
                "CLOUDFLARE_IMAGES_API_TOKEN or CLOUDFLARE_API_TOKEN is required"
            )

    @classmethod
    def from_env(cls) -> CloudflareImagesStore:
        return cls(
            account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
            api_token=(
                os.getenv("CLOUDFLARE_IMAGES_API_TOKEN")
                or os.getenv("CLOUDFLARE_API_TOKEN", "")
            ),
            variant=os.getenv("CLOUDFLARE_IMAGES_VARIANT", "public"),
        )

    def save(
        self, image_bytes: bytes, content_type: str, image_id: str | None = None
    ) -> str:
        _validate_content_type(content_type)
        filename = _filename(image_id, content_type)
        with httpx.Client(
            timeout=self._timeout, transport=_sync_transport(self._transport)
        ) as client:
            response = client.post(
                self._upload_url(),
                headers=self._headers(),
                data=_upload_fields(image_id),
                files={"file": (filename, image_bytes, content_type)},
            )
        return _delivery_url(response, self._variant)

    async def save_async(
        self, image_bytes: bytes, content_type: str, image_id: str | None = None
    ) -> str:
        _validate_content_type(content_type)
        filename = _filename(image_id, content_type)
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=_async_transport(self._transport),
        ) as client:
            response = await client.post(
                self._upload_url(),
                headers=self._headers(),
                data=_upload_fields(image_id),
                files={"file": (filename, image_bytes, content_type)},
            )
        return _delivery_url(response, self._variant)

    def load(self, image_id: str) -> bytes | None:
        raise NotImplementedError(_CATALOG_ONLY)

    def get_url(self, image_id: str) -> str | None:
        raise NotImplementedError(_CATALOG_ONLY)

    def delete(self, image_id: str) -> bool:
        raise NotImplementedError(_CATALOG_ONLY)

    async def load_async(self, image_id: str) -> bytes | None:
        raise NotImplementedError(_CATALOG_ONLY)

    async def get_url_async(self, image_id: str) -> str | None:
        raise NotImplementedError(_CATALOG_ONLY)

    async def delete_async(self, image_id: str) -> bool:
        raise NotImplementedError(_CATALOG_ONLY)

    def generate_upload_signature(self, image_id: str, ttl: int = 300) -> dict:
        raise NotImplementedError(_CATALOG_ONLY)

    async def generate_upload_signature_async(
        self, image_id: str, ttl: int = 300
    ) -> dict:
        raise NotImplementedError(_CATALOG_ONLY)

    def _upload_url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._account_id}{_UPLOAD_PATH}"
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}


def select_delivery_url(variants: list[Any], variant: str) -> str:
    """Pick the configured Cloudflare Images variant, then public, then first HTTPS URL."""
    https_variants = [
        value.rstrip("/")
        for value in variants
        if isinstance(value, str) and value.startswith("https://")
    ]
    if not https_variants:
        raise RuntimeError("Cloudflare Images upload returned no delivery URL")
    suffix = f"/{variant.lstrip('/')}"
    for url in https_variants:
        if url.endswith(suffix):
            return url
    for url in https_variants:
        if url.endswith("/public"):
            return url
    return https_variants[0]


def _delivery_url(response: httpx.Response, variant: str) -> str:
    if response.status_code != 200:
        raise RuntimeError(
            f"Cloudflare Images upload failed: HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Cloudflare Images upload returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Cloudflare Images upload returned invalid JSON")
    if payload.get("success") is False:
        raise RuntimeError("Cloudflare Images upload failed")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Cloudflare Images upload returned no result")
    variants = result.get("variants")
    if not isinstance(variants, list):
        raise RuntimeError("Cloudflare Images upload returned no delivery URL")
    return select_delivery_url(variants, variant)


def _validate_content_type(content_type: str) -> None:
    if content_type not in {"image/jpeg", "image/png"}:
        raise ValueError(f"Unsupported content type: {content_type}")


def _filename(image_id: str | None, content_type: str) -> str:
    extension = "jpg" if content_type == "image/jpeg" else "png"
    return f"{image_id or uuid.uuid4()}.{extension}"


def _upload_fields(image_id: str | None) -> dict[str, str]:
    fields = {"requireSignedURLs": "false"}
    if image_id:
        fields["id"] = image_id
    return fields


def _sync_transport(
    transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None,
) -> httpx.BaseTransport | None:
    return transport if isinstance(transport, httpx.BaseTransport) else None


def _async_transport(
    transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None,
) -> httpx.AsyncBaseTransport | None:
    return transport if isinstance(transport, httpx.AsyncBaseTransport) else None
