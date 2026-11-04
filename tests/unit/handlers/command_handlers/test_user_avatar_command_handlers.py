"""Avatar command handlers over the fake unit of work and in-memory image store."""

from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork
from tests.fixtures.mock_image_store import MockImageStore

from src.api.exceptions import (
    ExternalServiceException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.user import UpdateUserAvatarCommand, UploadUserAvatarPhotoCommand
from src.app.handlers.command_handlers.update_user_avatar_command_handler import (
    UpdateUserAvatarCommandHandler,
)
from src.app.handlers.command_handlers.upload_user_avatar_photo_command_handler import (
    UploadUserAvatarPhotoCommandHandler,
)
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.user import UserDomainModel
from src.domain.model.user.user_avatar import AvatarKind, UserAvatar

OLD_IMAGE_ID = "old-avatar-image"


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 30), "purple").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def store() -> MockImageStore:
    image_store = MockImageStore()
    image_store.storage[OLD_IMAGE_ID] = b"old"
    return image_store


async def _user(uow: FakeUnitOfWork, avatar: UserAvatar | None = None) -> str:
    user = UserDomainModel(
        firebase_uid="firebase-avatar",
        email="avatar@example.com",
        username="avatar",
        password_hash="",
        provider=AuthProvider.GOOGLE,
        avatar=avatar or UserAvatar(),
    )
    await uow.users.save(user)
    return str(user.id)


def _stored_avatar(uow: FakeUnitOfWork, user_id: str) -> UserAvatar:
    return next(u for u in uow.users.users.values() if str(u.id) == user_id).avatar


class TestUpdateUserAvatar:
    @pytest.mark.asyncio
    async def test_choosing_a_character_replaces_and_deletes_the_old_photo(
        self, uow, store
    ):
        user_id = await _user(uow, UserAvatar.photo(OLD_IMAGE_ID, "https://img/old"))
        handler = UpdateUserAvatarCommandHandler(lambda: uow, image_store=store)

        result = await handler.handle(
            UpdateUserAvatarCommand(
                user_id=user_id, kind="character", character="apple"
            )
        )

        assert result == {
            "kind": "character",
            "image_url": None,
            "character": "apple",
            "color": None,
        }
        assert _stored_avatar(uow, user_id) == UserAvatar.chosen(
            "character", character="apple"
        )
        assert OLD_IMAGE_ID not in store.storage
        assert uow.committed is True

    @pytest.mark.asyncio
    async def test_initials_colour_without_a_previous_photo_deletes_nothing(
        self, uow, store
    ):
        user_id = await _user(uow)
        store.delete_async = AsyncMock(return_value=True)
        handler = UpdateUserAvatarCommandHandler(lambda: uow, image_store=store)

        result = await handler.handle(
            UpdateUserAvatarCommand(user_id=user_id, kind="initials", color=5)
        )

        assert result["kind"] == "initials" and result["color"] == 5
        store.delete_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_choice_is_a_validation_error(self, uow, store):
        user_id = await _user(uow)
        handler = UpdateUserAvatarCommandHandler(lambda: uow, image_store=store)

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(
                UpdateUserAvatarCommand(user_id=user_id, kind="initials", color=8)
            )

        assert exc_info.value.error_code == "INVALID_AVATAR"
        assert _stored_avatar(uow, user_id).kind is AvatarKind.AUTO

    @pytest.mark.asyncio
    async def test_unknown_user_is_not_found(self, uow, store):
        handler = UpdateUserAvatarCommandHandler(lambda: uow, image_store=store)

        with pytest.raises(ResourceNotFoundException):
            await handler.handle(
                UpdateUserAvatarCommand(user_id="missing", kind="auto")
            )


class TestUploadUserAvatarPhoto:
    @pytest.mark.asyncio
    async def test_upload_stores_a_new_jpeg_and_deletes_the_old_photo(self, uow, store):
        user_id = await _user(uow, UserAvatar.photo(OLD_IMAGE_ID, "https://img/old"))
        handler = UploadUserAvatarPhotoCommandHandler(lambda: uow, store)

        result = await handler.handle(
            UploadUserAvatarPhotoCommand(user_id=user_id, image_bytes=_jpeg_bytes())
        )

        assert result["kind"] == "photo"
        assert result["character"] is None and result["color"] is None
        stored = _stored_avatar(uow, user_id)
        assert stored.image_url == result["image_url"]
        assert stored.image_id in store.storage
        assert stored.image_id != OLD_IMAGE_ID
        assert Image.open(BytesIO(store.storage[stored.image_id])).format == "JPEG"
        assert OLD_IMAGE_ID not in store.storage

    @pytest.mark.asyncio
    async def test_unreadable_upload_is_rejected_before_storage(self, uow, store):
        user_id = await _user(uow)
        handler = UploadUserAvatarPhotoCommandHandler(lambda: uow, store)

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(
                UploadUserAvatarPhotoCommand(user_id=user_id, image_bytes=b"GIF89a")
            )

        assert exc_info.value.error_code == "UNSUPPORTED_IMAGE_TYPE"
        assert set(store.storage) == {OLD_IMAGE_ID}

    @pytest.mark.asyncio
    async def test_new_image_is_deleted_when_the_row_update_fails(self, uow, store):
        handler = UploadUserAvatarPhotoCommandHandler(lambda: uow, store)

        with pytest.raises(ResourceNotFoundException):
            await handler.handle(
                UploadUserAvatarPhotoCommand(
                    user_id="missing", image_bytes=_jpeg_bytes()
                )
            )

        assert set(store.storage) == {OLD_IMAGE_ID}

    @pytest.mark.asyncio
    async def test_storage_failure_is_a_service_error(self, uow, store):
        user_id = await _user(uow)
        store.save_async = AsyncMock(side_effect=RuntimeError("cloudflare down"))
        handler = UploadUserAvatarPhotoCommandHandler(lambda: uow, store)

        with pytest.raises(ExternalServiceException):
            await handler.handle(
                UploadUserAvatarPhotoCommand(user_id=user_id, image_bytes=_jpeg_bytes())
            )

        assert _stored_avatar(uow, user_id).kind is AvatarKind.AUTO
