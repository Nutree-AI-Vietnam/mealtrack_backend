"""PATCH /v1/users/avatar and POST /v1/users/avatar/photo through the real handlers."""

from __future__ import annotations

import struct
import zlib
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork
from tests.fixtures.mock_image_store import MockImageStore

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import users as users_mod
from src.app.commands.user import UpdateUserAvatarCommand, UploadUserAvatarPhotoCommand
from src.app.handlers.command_handlers.update_user_avatar_command_handler import (
    UpdateUserAvatarCommandHandler,
)
from src.app.handlers.command_handlers.upload_user_avatar_photo_command_handler import (
    UploadUserAvatarPhotoCommandHandler,
)
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.user import UserDomainModel
from src.domain.model.user.user_avatar import AVATAR_PHOTO_MAX_BYTES
from src.infra.event_bus import PyMediatorEventBus


def _image(image_format: str, size=(48, 32)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "teal").save(buffer, format=image_format)
    return buffer.getvalue()


def _png_header_only(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


@pytest.fixture
def api():
    uow = FakeUnitOfWork()
    store = MockImageStore()
    user = UserDomainModel(
        firebase_uid="firebase-route",
        email="route@example.com",
        username="route",
        password_hash="",
        provider=AuthProvider.GOOGLE,
    )
    uow.users.users[user.id] = user
    bus = PyMediatorEventBus()
    bus.register_handler(
        UpdateUserAvatarCommand,
        UpdateUserAvatarCommandHandler(lambda: uow, image_store=store),
    )
    bus.register_handler(
        UploadUserAvatarPhotoCommand,
        UploadUserAvatarPhotoCommandHandler(lambda: uow, store),
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(users_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: str(user.id)
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    yield TestClient(app), user, store
    app.dependency_overrides.clear()


def _upload(client: TestClient, content: bytes, content_type: str = "image/jpeg"):
    return client.post(
        "/v1/users/avatar/photo",
        files={"file": ("avatar.jpg", content, content_type)},
    )


class TestPatchAvatar:
    def test_character_is_saved_and_returned(self, api):
        client, user, _ = api

        response = client.patch(
            "/v1/users/avatar", json={"kind": "character", "character": "broccoli"}
        )

        assert response.status_code == 200
        assert response.json() == {
            "kind": "character",
            "image_url": None,
            "character": "broccoli",
            "color": None,
        }
        assert user.avatar.character == "broccoli"

    @pytest.mark.parametrize(
        "body",
        [
            {"kind": "character", "character": "pho"},
            {"kind": "initials", "color": 8},
            {"kind": "initials", "color": -1},
            {"kind": "initials"},
            {"kind": "character", "character": "apple", "color": 2},
            {"kind": "photo"},
            {"kind": "emoji"},
        ],
    )
    def test_invalid_choice_is_rejected(self, api, body):
        client, user, _ = api

        response = client.patch("/v1/users/avatar", json=body)

        assert response.status_code == 422
        assert user.avatar.kind == "auto"

    def test_back_to_auto(self, api):
        client, _, _ = api
        client.patch("/v1/users/avatar", json={"kind": "initials", "color": 3})

        response = client.patch("/v1/users/avatar", json={"kind": "auto"})

        assert response.status_code == 200
        assert response.json()["kind"] == "auto"
        assert response.json()["color"] is None


class TestUploadAvatarPhoto:
    @pytest.mark.parametrize(
        ("image_format", "content_type"),
        [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
    )
    def test_supported_photo_becomes_the_avatar(self, api, image_format, content_type):
        client, user, store = api

        response = _upload(client, _image(image_format), content_type)

        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "photo"
        assert body["image_url"] == user.avatar.image_url
        assert user.avatar.image_id in store.storage

    def test_heic_content_type_is_unsupported(self, api):
        client, _, store = api

        response = _upload(client, b"\x00\x00\x00\x18ftypheic", "image/heic")

        assert response.status_code == 415
        assert response.json()["detail"]["error_code"] == "UNSUPPORTED_IMAGE_TYPE"
        assert store.storage == {}

    def test_tiff_labelled_as_jpeg_is_unsupported(self, api):
        client, user, store = api

        response = _upload(client, _image("TIFF"), "image/jpeg")

        assert response.status_code == 415
        assert user.avatar.kind == "auto"
        assert store.storage == {}

    def test_file_over_5_mib_is_too_large(self, api):
        client, _, store = api

        response = _upload(client, b"\xff" * (AVATAR_PHOTO_MAX_BYTES + 1))

        assert response.status_code == 413
        assert response.json()["detail"]["error_code"] == "IMAGE_TOO_LARGE"
        assert store.storage == {}

    def test_image_over_40_megapixels_is_too_large(self, api):
        client, _, store = api

        response = _upload(client, _png_header_only(10_000, 5_000), "image/png")

        assert response.status_code == 413
        assert store.storage == {}

    def test_missing_file_field_is_rejected(self, api):
        client, _, _ = api

        response = client.post("/v1/users/avatar/photo", files={"photo": ("a", b"x")})

        assert response.status_code == 422

    def test_eleventh_upload_in_an_hour_is_rate_limited(self, api):
        client, _, _ = api
        photo = _image("JPEG")

        statuses = [_upload(client, photo).status_code for _ in range(11)]

        assert statuses[:10] == [200] * 10
        assert statuses[10] == 429
