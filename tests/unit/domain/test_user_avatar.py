"""Rules for the avatar value type."""

import pytest

from src.domain.model.user.user_avatar import (
    AVATAR_CHARACTERS,
    AvatarKind,
    UserAvatar,
)


@pytest.mark.unit
class TestUserAvatarRules:
    def test_default_is_auto_with_nothing_stored(self):
        avatar = UserAvatar()

        assert avatar.kind is AvatarKind.AUTO
        assert avatar.to_public_dict() == {
            "kind": "auto",
            "image_url": None,
            "character": None,
            "color": None,
        }

    def test_eight_characters_are_allowed(self):
        assert AVATAR_CHARACTERS == {
            "avocado",
            "apple",
            "carrot",
            "broccoli",
            "lemon",
            "strawberry",
            "watermelon",
            "fried_egg",
        }
        for character in AVATAR_CHARACTERS:
            assert UserAvatar.chosen("character", character=character).character

    def test_unknown_character_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown avatar character"):
            UserAvatar.chosen("character", character="pho")

    @pytest.mark.parametrize("color", [-1, 8, True])
    def test_color_outside_the_palette_is_rejected(self, color):
        with pytest.raises(ValueError, match="color"):
            UserAvatar.chosen("initials", color=color)

    @pytest.mark.parametrize("color", [0, 7])
    def test_palette_edges_are_accepted(self, color: int):
        assert UserAvatar.chosen("initials", color=color).color == color

    @pytest.mark.parametrize(
        ("kind", "character", "color"),
        [
            ("character", None, None),
            ("initials", None, None),
            ("character", "apple", 3),
            ("auto", "apple", None),
            ("auto", None, 2),
        ],
    )
    def test_kind_takes_exactly_its_own_field(self, kind, character, color):
        with pytest.raises(ValueError, match=f"A {kind} avatar takes"):
            UserAvatar.chosen(kind, character=character, color=color)

    def test_photo_cannot_be_chosen_without_an_upload(self):
        with pytest.raises(ValueError, match="uploading"):
            UserAvatar.chosen("photo")

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError):
            UserAvatar.chosen("emoji")

    def test_photo_needs_an_image_id_and_url(self):
        with pytest.raises(ValueError):
            UserAvatar.photo("", "https://img.example/a")
        with pytest.raises(ValueError):
            UserAvatar.photo("image-1", "  ")
        with pytest.raises(ValueError):
            UserAvatar.photo("x" * 65, "https://img.example/a")

    def test_public_dict_hides_the_stored_image_id(self):
        avatar = UserAvatar.photo("image-1", "https://img.example/a")

        assert avatar.to_public_dict() == {
            "kind": "photo",
            "image_url": "https://img.example/a",
            "character": None,
            "color": None,
        }

    def test_kind_given_as_text_becomes_the_enum(self):
        avatar = UserAvatar(kind="initials", color=4)  # type: ignore[arg-type]

        assert avatar.kind is AvatarKind.INITIALS
