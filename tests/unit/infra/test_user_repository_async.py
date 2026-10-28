"""Unit tests for identity-specific persistence behavior."""

from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import IntegrityError

from src.domain.exceptions.firebase_identity_exceptions import (
    FirebaseIdentityConflictError,
)
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.user import UserDomainModel
from src.infra.repositories.user_repository_async import AsyncUserRepository


class _NoRowResult:
    def scalars(self):
        return self

    def first(self):
        return None


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["users_email_key", "users_firebase_uid_key"])
async def test_save_translates_identity_unique_constraint_to_conflict(
    constraint: str,
) -> None:
    session = Mock()
    session.execute = AsyncMock(return_value=_NoRowResult())
    session.begin_nested = Mock(return_value=_Savepoint())
    session.flush = AsyncMock(
        side_effect=IntegrityError(
            "INSERT",
            {},
            Exception(f'duplicate key value violates unique constraint "{constraint}"'),
        )
    )
    repository = AsyncUserRepository(session)
    user = UserDomainModel(
        id=None,
        firebase_uid="firebase-uid",
        email="buyer@example.com",
        username="buyer",
        password_hash="",
        provider=AuthProvider.GOOGLE,
    )

    with pytest.raises(FirebaseIdentityConflictError):
        await repository.save(user)

    session.begin_nested.assert_called_once()
    session.rollback.assert_not_called()


class _RowResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


@pytest.mark.asyncio
async def test_save_does_not_reload_user_after_flush() -> None:
    """Inserting a user must not SELECT that row again after flush.

    PYTHON-JY: AsyncUserRepository.save flushed, then re-selected the same
    user so mapper access would not trip lazy="raise". Profiles are already
    populated on the pending instance, so that second SELECT is redundant.
    """
    added: dict[str, object] = {}
    execute_count = 0

    async def execute(*_args, **_kwargs):
        nonlocal execute_count
        execute_count += 1
        if execute_count == 1:
            return _RowResult(None)
        return _RowResult(added.get("entity"))

    def add(entity):
        added["entity"] = entity

    session = Mock()
    session.execute = AsyncMock(side_effect=execute)
    session.begin_nested = Mock(return_value=_Savepoint())
    session.flush = AsyncMock()
    session.add = Mock(side_effect=add)

    repository = AsyncUserRepository(session)
    user = UserDomainModel(
        firebase_uid="firebase-new",
        email="new@example.com",
        username="newuser",
        password_hash="",
        provider=AuthProvider.GOOGLE,
        language_code="vi",
    )

    saved = await repository.save(user)

    assert execute_count == 1
    assert saved.firebase_uid == "firebase-new"
    assert saved.email == "new@example.com"
    assert saved.language_code == "vi"
    assert added["entity"].language_code == "vi"


def test_save_never_copies_the_avatar_columns() -> None:
    """Only update_user_avatar writes the avatar; a stale model must not."""
    from src.infra.repositories.user_repository_async import _USER_SAVE_COLUMNS

    assert not [name for name in _USER_SAVE_COLUMNS if name.startswith("avatar")]


def test_mapper_writes_no_avatar_and_reads_a_bad_stored_avatar_as_auto() -> None:
    from src.domain.model.user.user_avatar import UserAvatar
    from src.infra.database.models.user.user import User
    from src.infra.mappers.user_mapper import UserMapper, avatar_from_entity

    user = UserDomainModel(
        firebase_uid="firebase-avatar-map",
        email="map@example.com",
        username="mapuser",
        password_hash="",
        provider=AuthProvider.GOOGLE,
        avatar=UserAvatar.chosen("initials", color=4),
    )
    entity = UserMapper.to_persistence(user)
    assert entity.avatar_kind is None
    assert entity.avatar_color is None

    stored = User(id="u-1", avatar_kind="character", avatar_character="durian")
    assert avatar_from_entity(stored) == UserAvatar()
    stored_photo = User(
        id="u-2",
        avatar_kind="photo",
        avatar_image_id="img",
        avatar_image_url="https://img.example/img",
    )
    assert avatar_from_entity(stored_photo) == UserAvatar.photo(
        "img", "https://img.example/img"
    )
