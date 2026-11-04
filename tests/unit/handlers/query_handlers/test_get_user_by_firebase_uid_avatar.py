"""The profile returned by GET /v1/users/firebase/{uid} carries the avatar."""

from unittest.mock import patch

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.api.schemas.response.user_responses import UserProfileResponse
from src.app.handlers.query_handlers.get_user_by_firebase_uid_query_handler import (
    GetUserByFirebaseUidQueryHandler,
)
from src.app.queries.user.get_user_by_firebase_uid_query import (
    GetUserByFirebaseUidQuery,
)
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.user import UserDomainModel
from src.domain.model.user.user_avatar import UserAvatar
from src.domain.utils.timezone_utils import utc_now

_UOW_PATH = (
    "src.app.handlers.query_handlers."
    "get_user_by_firebase_uid_query_handler.AsyncUnitOfWork"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "avatar",
    [
        UserAvatar(),
        UserAvatar.photo("img-1", "https://img.example/img-1"),
        UserAvatar.chosen("character", character="lemon"),
        UserAvatar.chosen("initials", color=6),
    ],
)
async def test_profile_returns_the_stored_avatar(avatar: UserAvatar):
    uow = FakeUnitOfWork()
    now = utc_now()
    user = UserDomainModel(
        firebase_uid="firebase-profile",
        email="profile@example.com",
        username="profile",
        password_hash="",
        provider=AuthProvider.GOOGLE,
        photo_url="https://provider.example/p.jpg",
        avatar=avatar,
        created_at=now,
        updated_at=now,
    )
    await uow.users.save(user)

    with patch(_UOW_PATH, return_value=uow):
        result = await GetUserByFirebaseUidQueryHandler().handle(
            GetUserByFirebaseUidQuery(firebase_uid="firebase-profile")
        )

    assert result["avatar"] == avatar.to_public_dict()
    assert "image_id" not in result["avatar"]
    # Rows read from the database carry string ids; the fake keeps a UUID.
    response = UserProfileResponse(**{**result, "id": str(result["id"])})
    assert response.avatar.kind == avatar.kind.value
    assert response.photo_url == "https://provider.example/p.jpg"
