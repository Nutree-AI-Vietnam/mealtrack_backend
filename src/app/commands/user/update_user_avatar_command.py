"""Command to choose an avatar that needs no upload."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class UpdateUserAvatarCommand(Command):
    """Set the avatar to auto, a Nutree character, or an initials colour."""

    user_id: str
    kind: str
    character: str | None = None
    color: int | None = None
