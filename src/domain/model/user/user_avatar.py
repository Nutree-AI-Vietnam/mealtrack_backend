"""The avatar a person chose, kept apart from the sign-in provider's photo."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Nutree food characters the app ships art for.
AVATAR_CHARACTERS = frozenset(
    {
        "avocado",
        "apple",
        "carrot",
        "broccoli",
        "lemon",
        "strawberry",
        "watermelon",
        "fried_egg",
    }
)
# Initials colours are indexes into the app's palette.
AVATAR_COLOR_COUNT = 8
# Largest avatar photo file accepted, before the server re-encodes it.
AVATAR_PHOTO_MAX_BYTES = 5 * 1024 * 1024
AVATAR_PHOTO_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
AVATAR_IMAGE_ID_MAX_LENGTH = 64


class AvatarKind(StrEnum):
    # The provider photo when there is one, otherwise initials on a colour
    # the app derives from the user id. Nothing else is stored.
    AUTO = "auto"
    PHOTO = "photo"
    CHARACTER = "character"
    INITIALS = "initials"


_FIELDS_BY_KIND: dict[AvatarKind, frozenset[str]] = {
    AvatarKind.AUTO: frozenset(),
    AvatarKind.PHOTO: frozenset({"image_id", "image_url"}),
    AvatarKind.CHARACTER: frozenset({"character"}),
    AvatarKind.INITIALS: frozenset({"color"}),
}


@dataclass(frozen=True)
class UserAvatar:
    """Each kind sets exactly its own fields; every other field stays None."""

    kind: AvatarKind = AvatarKind.AUTO
    image_id: str | None = None
    image_url: str | None = None
    character: str | None = None
    color: int | None = None

    def __post_init__(self) -> None:
        kind = AvatarKind(self.kind)
        object.__setattr__(self, "kind", kind)
        present = {
            name
            for name in ("image_id", "image_url", "character", "color")
            if getattr(self, name) is not None
        }
        expected = _FIELDS_BY_KIND[kind]
        if present != expected:
            wanted = ", ".join(sorted(expected)) or "no other field"
            raise ValueError(f"A {kind.value} avatar takes {wanted}")
        if kind is AvatarKind.CHARACTER and self.character not in AVATAR_CHARACTERS:
            raise ValueError(f"Unknown avatar character: {self.character}")
        if kind is AvatarKind.INITIALS and not _is_palette_index(self.color):
            raise ValueError(
                f"Avatar color must be between 0 and {AVATAR_COLOR_COUNT - 1}"
            )
        if kind is AvatarKind.PHOTO:
            image_id = self.image_id or ""
            if not image_id.strip() or len(image_id) > AVATAR_IMAGE_ID_MAX_LENGTH:
                raise ValueError("A photo avatar needs a stored image id")
            if not (self.image_url or "").strip():
                raise ValueError("A photo avatar needs an image URL")

    @classmethod
    def photo(cls, image_id: str, image_url: str) -> "UserAvatar":
        return cls(kind=AvatarKind.PHOTO, image_id=image_id, image_url=image_url)

    @classmethod
    def chosen(
        cls, kind: str, character: str | None = None, color: int | None = None
    ) -> "UserAvatar":
        """An avatar picked without an upload: auto, a character or a colour.

        A photo avatar only comes from an upload, which stores the image itself.
        """
        avatar_kind = AvatarKind(kind)
        if avatar_kind is AvatarKind.PHOTO:
            raise ValueError("A photo avatar is set by uploading a photo")
        return cls(kind=avatar_kind, character=character, color=color)

    def to_public_dict(self) -> dict[str, Any]:
        """What clients see. The stored image id stays on the server."""
        return {
            "kind": self.kind.value,
            "image_url": self.image_url,
            "character": self.character,
            "color": self.color,
        }


def _is_palette_index(color: int | None) -> bool:
    return (
        isinstance(color, int)
        and not isinstance(color, bool)
        and 0 <= color < AVATAR_COLOR_COUNT
    )
