"""Command to make an uploaded photo the avatar."""

from dataclasses import dataclass, field

from src.app.events.base import Command


@dataclass
class UploadUserAvatarPhotoCommand(Command):
    """Store an uploaded photo as the avatar; the server re-encodes it first."""

    user_id: str
    image_bytes: bytes = field(repr=False)
