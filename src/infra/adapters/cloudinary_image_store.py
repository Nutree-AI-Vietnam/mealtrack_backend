import logging
import os
import uuid

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from dotenv import load_dotenv

from src.domain.ports.image_store_port import ImageStorePort

# Load environment variables if not already loaded
load_dotenv()

logger = logging.getLogger(__name__)


class CloudinaryImageStore(ImageStorePort):
    """
    Implementation of ImageStorePort using Cloudinary cloud service.

    This class implements US-1.3 - Save the raw image bytes securely.
    """

    def __init__(self):
        """Initialize Cloudinary configuration."""
        cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
        api_key = os.getenv("CLOUDINARY_API_KEY")
        api_secret = os.getenv("CLOUDINARY_API_SECRET")

        logger.debug(f"Initializing CloudinaryImageStore with cloud_name: {cloud_name}")

        if not all([cloud_name, api_key, api_secret]):
            raise ValueError(
                "Missing Cloudinary configuration. Make sure CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET are set in .env file"
            )

        # Configure Cloudinary
        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)

        logger.debug("CloudinaryImageStore initialized successfully")

    def save(
        self, image_bytes: bytes, content_type: str, image_id: str | None = None
    ) -> str:
        """
        Save image bytes to Cloudinary.

        Args:
            image_bytes: The raw bytes of the image
            content_type: MIME type of the image ("image/jpeg" or "image/png")
            image_id: Optional pre-generated image ID to use (for parallel uploads)

        Returns:
            The URL of the saved image

        Raises:
            ValueError: If content_type is not supported or image is invalid
        """
        logger.debug(
            f"Saving image of type {content_type}, size {len(image_bytes)} bytes"
        )

        # Validate content type
        if content_type not in ["image/jpeg", "image/png"]:
            raise ValueError(f"Unsupported content type: {content_type}")

        # Use provided image_id or generate a new UUID
        if image_id is None:
            image_id = str(uuid.uuid4())
            logger.debug(f"Generated image_id: {image_id}")
        else:
            logger.debug(f"Using provided image_id: {image_id}")

        # Determine file extension from content type
        if content_type == "image/jpeg":
            file_extension = "jpg"
        elif content_type == "image/png":
            file_extension = "png"
        else:
            file_extension = "jpg"  # Default fallback

        # Upload to Cloudinary
        # Use the image_id with extension as the public_id in Cloudinary
        folder = "mealtrack"  # Use a folder for organization

        try:
            # Upload the image with explicit format
            logger.debug(f"Uploading to Cloudinary with public_id: {folder}/{image_id}")
            response = cloudinary.uploader.upload(
                image_bytes,
                public_id=f"{folder}/{image_id}",
                resource_type="image",
                format=file_extension,  # Explicitly set the format
                overwrite=True,
            )

            response_url = response.get("secure_url")

            if response_url:
                logger.debug("Cloudinary upload successful for image_id=%s", image_id)
                return response_url
            else:
                logger.warning(
                    f"'secure_url' not found in Cloudinary response. Returning fallback image_id: {image_id}"
                )
                return image_id

        except Exception as exc:
            logger.error("Cloudinary upload failed error_type=%s", type(exc).__name__)
            raise

    def load(self, image_id: str) -> bytes | None:
        """
        Load image bytes by ID from Cloudinary.

        Args:
            image_id: The ID of the image to load

        Returns:
            The raw bytes of the image if found, None otherwise
        """
        logger.debug(f"Loading image with ID: {image_id}")

        # Get the URL for the image
        url = self.get_url(image_id)

        if not url:
            logger.error(f"No URL found for image ID: {image_id}")
            return None

        # Fetch the image from Cloudinary
        try:
            logger.debug("Fetching Cloudinary image for image_id=%s", image_id)
            import httpx

            response = httpx.get(url)
            if response.status_code == 200:
                logger.debug("Image successfully fetched")
                return response.content
            else:
                logger.error(
                    f"Failed to fetch image. Status code: {response.status_code}"
                )
        except Exception as e:
            logger.error(f"Error fetching image: {str(e)}")
            pass

        return None

    def get_url(self, image_id: str) -> str | None:
        """Build a Cloudinary delivery URL locally — no Admin API or HEAD round-trip."""
        logger.debug("Getting URL for image ID: %s", image_id)
        cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME") or getattr(
            cloudinary.config(), "cloud_name", None
        )
        if not cloud_name:
            logger.error("CLOUDINARY_CLOUD_NAME not found")
            return None

        public_id = f"mealtrack/{image_id}"
        return f"https://res.cloudinary.com/{cloud_name}/image/upload/{public_id}"

    def delete(self, image_id: str) -> bool:
        """
        Delete an image by ID from Cloudinary.

        Args:
            image_id: The ID of the image to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        logger.debug(f"Deleting image with ID: {image_id}")
        folder = "mealtrack"  # Same folder used in other methods

        try:
            # Delete the image from Cloudinary
            response = cloudinary.uploader.destroy(f"{folder}/{image_id}")
            success = response.get("result") == "ok"
            logger.debug(f"Delete result: {success}")
            return success
        except Exception as e:
            logger.error(f"Error deleting image: {str(e)}")
            return False

    async def save_async(
        self, image_bytes: bytes, content_type: str, image_id: str | None = None
    ) -> str:
        """Async wrapper — runs blocking Cloudinary SDK upload off the event loop."""
        import asyncio

        return await asyncio.to_thread(self.save, image_bytes, content_type, image_id)

    async def load_async(self, image_id: str) -> bytes | None:
        """Async wrapper — runs blocking Cloudinary SDK + HTTP load off the event loop."""
        import asyncio

        return await asyncio.to_thread(self.load, image_id)

    async def get_url_async(self, image_id: str) -> str | None:
        """URL construction is local and non-blocking."""
        return self.get_url(image_id)

    async def delete_async(self, image_id: str) -> bool:
        """Async wrapper — runs blocking Cloudinary SDK delete off the event loop."""
        import asyncio

        return await asyncio.to_thread(self.delete, image_id)

    def generate_upload_signature(self, image_id: str, ttl: int = 300) -> dict:
        """Return signed Cloudinary upload params for direct client upload."""
        import time as _time

        folder = "mealtrack"
        public_id = f"{folder}/{image_id}"
        timestamp = int(_time.time())

        params_to_sign = {
            "public_id": public_id,
            "timestamp": timestamp,
        }
        api_secret = os.getenv("CLOUDINARY_API_SECRET", "")
        signature = cloudinary.utils.api_sign_request(params_to_sign, api_secret)

        return {
            "image_id": image_id,
            "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
            "api_key": os.getenv("CLOUDINARY_API_KEY", ""),
            "timestamp": timestamp,
            "signature": signature,
            "folder": folder,
            "public_id": public_id,
        }

    async def generate_upload_signature_async(
        self, image_id: str, ttl: int = 300
    ) -> dict:
        """Async wrapper for generate_upload_signature."""
        import asyncio

        return await asyncio.to_thread(self.generate_upload_signature, image_id, ttl)
