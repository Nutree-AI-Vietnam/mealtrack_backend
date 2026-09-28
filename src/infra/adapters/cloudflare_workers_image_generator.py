"""Cloudflare Workers AI image generation adapter."""

from __future__ import annotations

import base64

import httpx

from src.domain.ports.image_store_port import ImageStorePort


class CloudflareWorkersImageGenerator:
    """Generate image URLs through Cloudflare Workers AI."""

    name = "cloudflare-workers-ai"

    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        model: str = "@cf/black-forest-labs/flux-2-klein-9b",
        timeout: int = 120,
        transport: httpx.AsyncBaseTransport | None = None,
        image_store: ImageStorePort | None = None,
    ) -> None:
        self._account_id = account_id.strip()
        self._api_token = api_token.strip()
        self._model = model.strip()
        self._timeout = timeout
        self._transport = transport
        self._image_store = image_store
        if not self._account_id:
            raise ValueError("CLOUDFLARE_ACCOUNT_ID is required")
        if not self._api_token:
            raise ValueError("CLOUDFLARE_API_TOKEN is required")
        if not self._model:
            raise ValueError("Cloudflare image model is required")

    async def generate_url(
        self,
        prompt: str,
        *,
        image_id: str | None = None,
        quality: str = "medium",
        size: str = "1024x1024",
        output_format: str = "jpeg",
    ) -> str:
        if _requires_multipart(self._model):
            return await self._generate_multipart_url(
                prompt,
                image_id=image_id,
                size=size,
                output_format=output_format,
            )

        payload = {
            "model": self._model,
            "input": {
                "prompt": prompt,
                "quality": quality,
                "size": size,
                "output_format": output_format,
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }
        url = f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run"
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(
                f"Cloudflare image generation returned {response.status_code}: "
                f"{response.text[:200]}"
            )
        return await self._extract_image_url(
            response.json(), output_format, image_id=image_id
        )

    async def _generate_multipart_url(
        self,
        prompt: str,
        *,
        image_id: str | None = None,
        size: str,
        output_format: str,
    ) -> str:
        width, height = _dimensions(size)
        headers = {"Authorization": f"Bearer {self._api_token}"}
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._account_id}/ai/run/{self._model}"
        )
        files = {
            "prompt": (None, prompt),
            "width": (None, str(width)),
            "height": (None, str(height)),
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            response = await client.post(url, files=files, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(
                f"Cloudflare image generation returned {response.status_code}: "
                f"{response.text[:200]}"
            )
        return await self._extract_image_url(
            response.json(), output_format, image_id=image_id
        )

    async def _extract_image_url(
        self,
        payload: dict,
        output_format: str,
        image_id: str | None = None,
    ) -> str:
        image = _extract_image(payload)
        if image.startswith("https://"):
            return image
        if self._image_store is None:
            raise RuntimeError(
                "Cloudflare image generation returned base64 image data but no image store is configured"
            )
        image_bytes = _decode_image(image)
        content_type = _content_type(image_bytes, output_format)
        return await self._image_store.save_async(
            image_bytes, content_type, image_id=image_id
        )


def _extract_image(payload: dict) -> str:
    result = payload.get("result")
    if isinstance(result, dict):
        image = result.get("image")
        if isinstance(image, str) and image.strip():
            return image
    errors = payload.get("errors")
    if errors:
        raise RuntimeError(f"Cloudflare image generation failed: {errors}")
    raise RuntimeError("Cloudflare image generation response missing result.image")


def _requires_multipart(model: str) -> bool:
    return model.startswith("@cf/black-forest-labs/flux-2-")


def _dimensions(size: str) -> tuple[int, int]:
    try:
        width_raw, height_raw = size.lower().split("x", 1)
        width = int(width_raw)
        height = int(height_raw)
    except ValueError as exc:
        raise ValueError("Image size must use WIDTHxHEIGHT format") from exc
    return width, height


def _decode_image(image: str) -> bytes:
    if "," in image and image.startswith("data:"):
        image = image.split(",", 1)[1]
    return base64.b64decode(image, validate=True)


def _content_type(image_bytes: bytes, output_format: str) -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if output_format.lower() in {"jpg", "jpeg"}:
        return "image/jpeg"
    if output_format.lower() == "png":
        return "image/png"
    return "image/png"
