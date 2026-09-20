"""Cloudflare Images implementation of ImageStorePort."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from src.domain.ports.image_store_port import ImageStorePort
from src.infra.config.settings import get_settings

logger = logging.getLogger(__name__)


class CloudflareImageStore(ImageStorePort):
    """Implementation of ImageStorePort using Cloudflare Images service.

    Supports direct creator uploads, direct server-side uploads, on-the-fly variants,
    and deletion.
    """

    def __init__(
        self,
        *,
        account_id: str | None = None,
        api_token: str | None = None,
        account_hash: str | None = None,
        default_variant: str | None = None,
        custom_domain: str | None = None,
        client: httpx.AsyncClient | None = None,
        sync_client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self._account_id = (
            account_id if account_id is not None else settings.CLOUDFLARE_ACCOUNT_ID
        ).strip()
        self._api_token = (
            api_token if api_token is not None else settings.CLOUDFLARE_API_TOKEN
        ).strip()
        self._account_hash = (
            account_hash
            if account_hash is not None
            else settings.CLOUDFLARE_ACCOUNT_HASH
        ).strip()
        self._default_variant = (
            default_variant
            if default_variant is not None
            else settings.CLOUDFLARE_DEFAULT_VARIANT
        ).strip() or "public"
        raw_custom_domain = (
            custom_domain
            if custom_domain is not None
            else settings.CLOUDFLARE_CUSTOM_DOMAIN
        )
        if raw_custom_domain:
            cleaned = raw_custom_domain.strip().lower()
            if "://" in cleaned:
                cleaned = cleaned.split("://", 1)[1]
            self._custom_domain = cleaned.split("/")[0]
        else:
            self._custom_domain = ""
        self._client = client
        self._sync_client = sync_client
        self._timeout = timeout
        self._base_api_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/images"
            if self._account_id
            else ""
        )

    def _ensure_configured(self) -> None:
        if not self._account_id or not self._api_token:
            raise ValueError(
                "Missing Cloudflare configuration. Make sure CLOUDFLARE_ACCOUNT_ID "
                "and CLOUDFLARE_API_TOKEN are set."
            )
        if not self._account_hash and not self._custom_domain:
            raise ValueError(
                "Missing Cloudflare image delivery configuration. Make sure "
                "CLOUDFLARE_ACCOUNT_HASH or CLOUDFLARE_CUSTOM_DOMAIN is set."
            )

    def get_url(self, image_id: str, variant: str | None = None) -> str | None:
        """Construct delivery URL for a Cloudflare Images image."""
        if not image_id:
            return None
        v = variant or self._default_variant
        if self._custom_domain:
            return f"https://{self._custom_domain}/{image_id}/{v}"
        if not self._account_hash:
            logger.warning(
                "CLOUDFLARE_ACCOUNT_HASH (or CLOUDFLARE_CUSTOM_DOMAIN) is required "
                "to construct Cloudflare Images delivery URLs."
            )
            return None
        return f"https://imagedelivery.net/{self._account_hash}/{image_id}/{v}"

    async def get_url_async(
        self, image_id: str, variant: str | None = None
    ) -> str | None:
        """Async version of get_url."""
        return self.get_url(image_id, variant)

    @staticmethod
    def _normalize_content_type(content_type: str) -> str:
        """Normalize MIME type by stripping parameters and mapping common aliases."""
        clean_type = content_type.split(";")[0].strip().lower()
        if clean_type == "image/jpg":
            return "image/jpeg"
        return clean_type

    def save(
        self,
        image_bytes: bytes,
        content_type: str,
        image_id: str | None = None,
    ) -> str:
        """Synchronously upload image bytes to Cloudflare Images."""
        self._ensure_configured()
        normalized_content_type = self._normalize_content_type(content_type)
        if normalized_content_type not in [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
        ]:
            raise ValueError(f"Unsupported content type: {content_type}")

        if image_id is None:
            image_id = str(uuid.uuid4())

        url = f"{self._base_api_url}/v1"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        files = {"file": (f"{image_id}.jpg", image_bytes, normalized_content_type)}
        data = {"id": image_id}

        client = self._sync_client or httpx.Client(timeout=self._timeout)
        try:
            response = client.post(url, files=files, data=data, headers=headers)
        finally:
            if client is not self._sync_client:
                client.close()

        if response.status_code != 200:
            raise RuntimeError(
                f"Cloudflare Images upload failed with status {response.status_code}: {response.text[:200]}"
            )

        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(
                f"Cloudflare Images upload failed: {payload.get('errors')}"
            )

        result = payload.get("result", {})
        variants = result.get("variants") or []
        delivery_url = self._select_delivery_url(variants, image_id)
        if not delivery_url:
            raise RuntimeError(
                f"Cloudflare Images upload succeeded for {image_id}, but failed to resolve a delivery URL."
            )
        return delivery_url

    def _select_delivery_url(self, variants: list[str], image_id: str) -> str:
        """Select the preferred delivery URL for an image.

        Prioritizes the configured default variant and custom domain over
        arbitrary variant ordering returned by Cloudflare.
        """
        if self._custom_domain:
            for v in variants:
                if self._custom_domain in v and v.rstrip("/").endswith(
                    f"/{self._default_variant}"
                ):
                    return v
            constructed = self.get_url(image_id)
            if constructed:
                return constructed

        for v in variants:
            if v.rstrip("/").endswith(f"/{self._default_variant}"):
                return v

        constructed = self.get_url(image_id)
        if constructed:
            return constructed

        return variants[0] if variants else ""

    async def save_async(
        self,
        image_bytes: bytes,
        content_type: str,
        image_id: str | None = None,
    ) -> str:
        """Asynchronously upload image bytes to Cloudflare Images."""
        self._ensure_configured()
        normalized_content_type = self._normalize_content_type(content_type)
        if normalized_content_type not in [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
        ]:
            raise ValueError(f"Unsupported content type: {content_type}")

        if image_id is None:
            image_id = str(uuid.uuid4())

        url = f"{self._base_api_url}/v1"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        files = {"file": (f"{image_id}.jpg", image_bytes, normalized_content_type)}
        data = {"id": image_id}

        if self._client:
            response = await self._client.post(
                url, files=files, data=data, headers=headers, timeout=self._timeout
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url, files=files, data=data, headers=headers
                )

        if response.status_code != 200:
            raise RuntimeError(
                f"Cloudflare Images upload failed with status {response.status_code}: {response.text[:200]}"
            )

        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(
                f"Cloudflare Images upload failed: {payload.get('errors')}"
            )

        result = payload.get("result", {})
        variants = result.get("variants") or []
        delivery_url = self._select_delivery_url(variants, image_id)
        if not delivery_url:
            raise RuntimeError(
                f"Cloudflare Images upload succeeded for {image_id}, but failed to resolve a delivery URL."
            )
        return delivery_url

    def load(self, image_id: str) -> bytes | None:
        """Synchronously load image bytes from Cloudflare Images."""
        url = self.get_url(image_id)
        if not url:
            return None
        client = self._sync_client or httpx.Client(timeout=10.0)
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.content
            logger.warning(
                "Failed to fetch Cloudflare image %s (status %d)",
                image_id,
                resp.status_code,
            )
            return None
        except Exception as exc:
            logger.error("Error fetching Cloudflare image %s: %s", image_id, exc)
            return None
        finally:
            if client is not self._sync_client:
                client.close()

    async def load_async(self, image_id: str) -> bytes | None:
        """Asynchronously load image bytes from Cloudflare Images."""
        url = self.get_url(image_id)
        if not url:
            return None
        try:
            if self._client:
                resp = await self._client.get(url, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
            logger.warning(
                "Failed to fetch Cloudflare image %s (status %d)",
                image_id,
                resp.status_code,
            )
            return None
        except Exception as exc:
            logger.error("Error fetching Cloudflare image %s: %s", image_id, exc)
            return None

    def delete(self, image_id: str) -> bool:
        """Synchronously delete image from Cloudflare Images."""
        self._ensure_configured()
        url = f"{self._base_api_url}/v1/{image_id}"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        client = self._sync_client or httpx.Client(timeout=10.0)
        try:
            resp = client.delete(url, headers=headers)
            if resp.status_code in (200, 404):
                return True
            logger.warning(
                "Failed to delete Cloudflare image %s: %s",
                image_id,
                resp.text[:200],
            )
            return False
        except Exception as exc:
            logger.error("Error deleting Cloudflare image %s: %s", image_id, exc)
            return False
        finally:
            if client is not self._sync_client:
                client.close()

    async def delete_async(self, image_id: str) -> bool:
        """Asynchronously delete image from Cloudflare Images."""
        self._ensure_configured()
        url = f"{self._base_api_url}/v1/{image_id}"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        try:
            if self._client:
                resp = await self._client.delete(url, headers=headers, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.delete(url, headers=headers)
            if resp.status_code in (200, 404):
                return True
            logger.warning(
                "Failed to delete Cloudflare image %s: %s",
                image_id,
                resp.text[:200],
            )
            return False
        except Exception as exc:
            logger.error("Error deleting Cloudflare image %s: %s", image_id, exc)
            return False

    def generate_upload_signature(self, image_id: str, ttl: int = 300) -> dict:
        """Synchronously request a direct upload URL from Cloudflare Images."""
        self._ensure_configured()
        url = f"{self._base_api_url}/v2/direct_upload"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        expiry = (datetime.now(UTC) + timedelta(seconds=max(120, ttl))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        data = {"expiry": expiry, "id": image_id}

        client = self._sync_client or httpx.Client(timeout=10.0)
        try:
            resp = client.post(url, data=data, headers=headers)
        finally:
            if client is not self._sync_client:
                client.close()

        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to generate Cloudflare upload URL: {resp.status_code} - {resp.text[:200]}"
            )

        payload = resp.json()
        if not payload.get("success"):
            raise RuntimeError(
                f"Cloudflare direct upload failed: {payload.get('errors')}"
            )

        result = payload.get("result", {})
        upload_url = result.get("uploadURL")
        returned_id = result.get("id", image_id)

        return {
            "image_id": returned_id,
            "upload_url": upload_url,
            "provider": "cloudflare",
            "cloud_name": "",
            "api_key": "",
            "timestamp": int(time.time()),
            "signature": "",
            "folder": "mealtrack",
            "public_id": f"mealtrack/{returned_id}",
        }

    async def generate_upload_signature_async(
        self, image_id: str, ttl: int = 300
    ) -> dict:
        """Asynchronously request a direct upload URL from Cloudflare Images."""
        self._ensure_configured()
        url = f"{self._base_api_url}/v2/direct_upload"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        expiry = (datetime.now(UTC) + timedelta(seconds=max(120, ttl))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        data = {"expiry": expiry, "id": image_id}

        if self._client:
            resp = await self._client.post(
                url, data=data, headers=headers, timeout=10.0
            )
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, data=data, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to generate Cloudflare upload URL: {resp.status_code} - {resp.text[:200]}"
            )

        payload = resp.json()
        if not payload.get("success"):
            raise RuntimeError(
                f"Cloudflare direct upload failed: {payload.get('errors')}"
            )

        result = payload.get("result", {})
        upload_url = result.get("uploadURL")
        returned_id = result.get("id", image_id)

        return {
            "image_id": returned_id,
            "upload_url": upload_url,
            "provider": "cloudflare",
            "cloud_name": "",
            "api_key": "",
            "timestamp": int(time.time()),
            "signature": "",
            "folder": "mealtrack",
            "public_id": f"mealtrack/{returned_id}",
        }
