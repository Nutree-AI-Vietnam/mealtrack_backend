import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class MealImage:
    """
    Value object representing a meal image.
    Contains metadata about the image like format, size, and a unique identifier.
    """

    image_id: str  # UUID as string
    format: str  # "jpeg" or "png"
    size_bytes: int
    width: int | None = None
    height: int | None = None
    url: str | None = None

    def __post_init__(self):
        """Validate invariants."""
        # Validate UUID format
        try:
            uuid.UUID(self.image_id)
        except ValueError as e:
            raise ValueError(
                f"Invalid UUID format for image_id: {self.image_id}"
            ) from e

        # Validate format
        if self.format.lower() not in ["jpeg", "png"]:
            raise ValueError(
                f"Image format must be 'jpeg' or 'png', got: {self.format}"
            )

        # Validate size
        if self.size_bytes <= 0:
            raise ValueError(f"Size must be positive: {self.size_bytes}")

        # Max size check (8MB)
        max_size = 8 * 1024 * 1024  # 8MB in bytes
        if self.size_bytes > max_size:
            raise ValueError(
                f"Image size exceeds maximum allowed (8MB): {self.size_bytes} bytes"
            )

        # Validate dimensions if provided
        if self.width is not None and self.width <= 0:
            raise ValueError(f"Width must be positive: {self.width}")

        if self.height is not None and self.height <= 0:
            raise ValueError(f"Height must be positive: {self.height}")

    @property
    def is_placeholder(self) -> bool:
        """True for the stand-in row of a meal logged without a photo.

        Text, manual, hydration and suggestion meals store a 1-byte image
        with no URL. It has no delivery URL to build: clients must see no
        photo rather than one that never loads.
        """
        return not self.url and self.size_bytes <= 1

    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        result = {
            "image_id": self.image_id,
            "format": self.format,
            "size_bytes": self.size_bytes,
        }

        if self.width is not None:
            result["width"] = self.width

        if self.height is not None:
            result["height"] = self.height

        if self.url is not None:
            result["url"] = self.url

        return result
