"""Local disk implementation of ImageStorePort for development and local testing."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from src.domain.ports.image_store_port import ImageStorePort

logger = logging.getLogger(__name__)

CONTENT_TYPE_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class LocalImageStore(ImageStorePort):
    """Stores uploaded and generated images directly on the local filesystem."""

    def __init__(
        self,
        upload_dir: str | Path = "./uploads",
        base_url: str = "http://localhost:8000",
    ) -> None:
        self._upload_dir = Path(upload_dir).resolve()
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url.rstrip("/")

    def save(
        self,
        image_bytes: bytes,
        content_type: str,
        image_id: str | None = None,
    ) -> str:
        ext = CONTENT_TYPE_MAP.get(content_type.lower(), ".jpg")
        name = image_id or uuid.uuid4().hex
        if not name.endswith(ext):
            filename = f"{name}{ext}"
        else:
            filename = name

        file_path = self._upload_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(image_bytes)

        # Calculate relative path from upload_dir
        rel_path = file_path.relative_to(self._upload_dir).as_posix()
        url = f"{self._base_url}/uploads/{rel_path}"
        logger.info("Saved local image: %s -> %s", file_path, url)
        return url

    def load(self, image_id: str) -> bytes | None:
        # Search for file matching image_id
        for path in self._upload_dir.rglob(f"{image_id}*"):
            if path.is_file():
                return path.read_bytes()
        return None

    def get_url(self, image_id: str) -> str | None:
        for path in self._upload_dir.rglob(f"{image_id}*"):
            if path.is_file():
                rel_path = path.relative_to(self._upload_dir).as_posix()
                return f"{self._base_url}/uploads/{rel_path}"
        return None

    def delete(self, image_id: str) -> bool:
        deleted = False
        for path in self._upload_dir.rglob(f"{image_id}*"):
            if path.is_file():
                path.unlink()
                deleted = True
        return deleted

    async def save_async(
        self,
        image_bytes: bytes,
        content_type: str,
        image_id: str | None = None,
    ) -> str:
        return await asyncio.to_thread(self.save, image_bytes, content_type, image_id)

    async def load_async(self, image_id: str) -> bytes | None:
        return await asyncio.to_thread(self.load, image_id)

    async def get_url_async(self, image_id: str) -> str | None:
        return await asyncio.to_thread(self.get_url, image_id)

    async def delete_async(self, image_id: str) -> bool:
        return await asyncio.to_thread(self.delete, image_id)

    def generate_upload_signature(self, image_id: str, ttl: int = 300) -> dict:
        ext = ".jpg"
        filename = f"{image_id}{ext}"
        return {
            "upload_url": f"{self._base_url}/uploads/{filename}",
            "image_id": image_id,
            "provider": "local",
        }

    async def generate_upload_signature_async(
        self, image_id: str, ttl: int = 300
    ) -> dict:
        return await asyncio.to_thread(self.generate_upload_signature, image_id, ttl)
