from abc import ABC, abstractmethod


class ImageStorePort(ABC):
    """Port interface for image storage operations."""

    @abstractmethod
    def save(
        self, image_bytes: bytes, content_type: str, image_id: str | None = None
    ) -> str:
        """
        Saves image bytes to storage.

        Args:
            image_bytes: The raw bytes of the image
            content_type: MIME type of the image ("image/jpeg" or "image/png")
            image_id: Optional pre-generated image ID to use (for parallel uploads)

        Returns:
            The URL of the saved image

        Raises:
            ValueError: If content_type is not supported or image is invalid
        """
        pass

    @abstractmethod
    def load(self, image_id: str) -> bytes | None:
        """
        Loads image bytes by ID.

        Args:
            image_id: The ID of the image to load

        Returns:
            The raw bytes of the image if found, None otherwise
        """
        pass

    @abstractmethod
    def get_url(self, image_id: str) -> str | None:
        """
        Gets a URL for accessing the image, if applicable.

        Args:
            image_id: The ID of the image

        Returns:
            URL to access the image if available, None otherwise
        """
        pass

    @abstractmethod
    def delete(self, image_id: str) -> bool:
        """
        Deletes an image by ID.

        Args:
            image_id: The ID of the image to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        pass

    @abstractmethod
    async def save_async(
        self, image_bytes: bytes, content_type: str, image_id: str | None = None
    ) -> str:
        """Async version of save. Implementations should use asyncio.to_thread for sync SDKs."""
        pass

    @abstractmethod
    async def load_async(self, image_id: str) -> bytes | None:
        """Async version of load."""
        pass

    @abstractmethod
    async def get_url_async(self, image_id: str) -> str | None:
        """Async version of get_url."""
        pass

    @abstractmethod
    async def delete_async(self, image_id: str) -> bool:
        """Async version of delete."""
        pass

    @abstractmethod
    def generate_upload_signature(self, image_id: str, ttl: int = 300) -> dict:
        """Return signed upload params or direct upload URL for direct client upload.

        Args:
            image_id: Unique identifier for the image.
            ttl: Validity window in seconds (default 300).

        Returns:
            Dict containing upload details (e.g. upload_url, image_id, provider,
            and any provider-specific parameters).
        """
        pass

    @abstractmethod
    async def generate_upload_signature_async(
        self, image_id: str, ttl: int = 300
    ) -> dict:
        """Async version of generate_upload_signature."""
        pass
