"""Cloudflare Images implementation of ImageStorePort."""

from __future__ import annotations

import logging
import uuid
from urllib.parse import quote

import httpx

from src.domain.ports.image_store_port import ImageStorePort
from src.infra.adapters.meal_photo_upload import issue_upload_ticket
from src.infra.config.settings import get_settings

logger = logging.getLogger(__name__)


class CloudflareImageStore(ImageStorePort):
    """Implementation of ImageStorePort using Cloudflare Images service.

    Server-side saves still use the Cloudflare Images API. Client upload tokens
    are signed locally and stored in R2, so they do not use that API quota.
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
        api_public_base_url: str | None = None,
        r2_access_key_id: str | None = None,
        r2_secret_access_key: str | None = None,
        r2_bucket: str | None = None,
        r2_public_base_url: str | None = None,
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
        self._api_public_base_url = (
            api_public_base_url
            if api_public_base_url is not None
            else settings.API_PUBLIC_BASE_URL
        ).strip()
        self._r2_access_key_id = (
            r2_access_key_id
            if r2_access_key_id is not None
            else settings.R2_ACCESS_KEY_ID
        ).strip()
        self._r2_secret_access_key = (
            r2_secret_access_key
            if r2_secret_access_key is not None
            else settings.R2_SECRET_ACCESS_KEY
        ).strip()
        self._r2_bucket = (
            r2_bucket if r2_bucket is not None else settings.R2_BUCKET
        ).strip()
        self._r2_public_base_url = (
            r2_public_base_url
            if r2_public_base_url is not None
            else settings.R2_PUBLIC_BASE_URL
        ).strip()
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

    @staticmethod
    def _to_cloudflare_id(image_id: str) -> str:
        """Ensure custom ID conforms to Cloudflare Images rules.

        Cloudflare Images requires that custom IDs must not be a bare UUID.
        If image_id is a UUID, prefix it with 'mealtrack/' to create a valid subpath ID.
        """
        if not image_id:
            return image_id
        try:
            uuid.UUID(image_id)
            return f"mealtrack/{image_id}"
        except ValueError:
            return image_id

    def get_url(self, image_id: str, variant: str | None = None) -> str | None:
        """Construct delivery URL for a Cloudflare Images image."""
        if not image_id:
            return None
        cf_id = self._to_cloudflare_id(image_id)
        v = variant or self._default_variant
        if self._custom_domain:
            return f"https://{self._custom_domain}/{cf_id}/{v}"
        if not self._account_hash:
            logger.warning(
                "CLOUDFLARE_ACCOUNT_HASH (or CLOUDFLARE_CUSTOM_DOMAIN) is required "
                "to construct Cloudflare Images delivery URLs."
            )
            return None
        return f"https://imagedelivery.net/{self._account_hash}/{cf_id}/{v}"

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

        cf_id = self._to_cloudflare_id(image_id)

        url = f"{self._base_api_url}/v1"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        files = {"file": (f"{image_id}.jpg", image_bytes, normalized_content_type)}
        data = {"id": cf_id}

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
        delivery_url = self._select_delivery_url(variants, cf_id)
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

        cf_id = self._to_cloudflare_id(image_id)

        url = f"{self._base_api_url}/v1"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        files = {"file": (f"{image_id}.jpg", image_bytes, normalized_content_type)}
        data = {"id": cf_id}

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
        delivery_url = self._select_delivery_url(variants, cf_id)
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
        cf_id = self._to_cloudflare_id(image_id)
        url = f"{self._base_api_url}/v1/{quote(cf_id, safe='')}"
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
        cf_id = self._to_cloudflare_id(image_id)
        url = f"{self._base_api_url}/v1/{quote(cf_id, safe='')}"
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
        """Sign a client upload locally. Does not call Cloudflare Images."""
        self._ensure_r2_upload_configured()
        return issue_upload_ticket(
            image_id=image_id,
            ttl=ttl,
            api_public_base_url=self._api_public_base_url,
            signing_secret=self._r2_secret_access_key,
        )

    async def generate_upload_signature_async(
        self, image_id: str, ttl: int = 300
    ) -> dict:
        """Local signature. No network call."""
        return self.generate_upload_signature(image_id, ttl)

    def _ensure_r2_upload_configured(self) -> None:
        missing = [
            name
            for name, value in (
                ("API_PUBLIC_BASE_URL", self._api_public_base_url),
                ("R2_ACCESS_KEY_ID", self._r2_access_key_id),
                ("R2_SECRET_ACCESS_KEY", self._r2_secret_access_key),
                ("R2_BUCKET", self._r2_bucket),
                ("R2_PUBLIC_BASE_URL", self._r2_public_base_url),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing R2 meal photo configuration: " + ", ".join(missing)
            )
