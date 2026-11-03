"""Admin photo routes for catalog supplements: auth, upload checks, status codes."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.base_dependencies import get_image_store
from src.api.dependencies.auth import require_admin_or_local
from src.api.routes.v1 import admin_supplement_images as route_mod
from src.app.services.supplement_product_photo import SupplementPhotoStorageError
from src.bootstrap.supplement_catalog import SupplementCatalogUnavailableError
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementProduct,
    SupplementProductNotFoundError,
)
from src.domain.model.supplement.catalog_review import SupplementNotReviewableError
from src.domain.model.user.user_avatar import AVATAR_PHOTO_MAX_BYTES
from src.domain.ports.supplement_catalog_port import ReviewItem
from src.domain.utils.avatar_image import (
    AvatarImageTooLargeError,
    UnsupportedAvatarImageError,
)

STORE = object()
JPEG = ("bottle.jpg", b"\xff\xd8\xff photo", "image/jpeg")


def _client(admin: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(route_mod.router)

    async def deny():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin_or_local] = (
        (lambda: "admin@nutreeai.com") if admin else deny
    )
    app.dependency_overrides[get_image_store] = lambda: STORE
    return TestClient(app)


def _item(image_url: str | None = "https://img.test/bottle.jpg") -> ReviewItem:
    product = SupplementProduct(
        id="dsld-1",
        name="Vitamin D3 1000 IU",
        brand="Nature Made",
        form=SupplementForm.SOFTGEL,
        unit_label="viên nang mềm",
        source=ProductSource.CURATED,
        review_status=ReviewStatus.APPROVED,
        catalog_key="dsld:2959",
        image_id="img-1" if image_url else None,
        image_url=image_url,
    )
    return ReviewItem(product=product)


@pytest.mark.parametrize("method", ["post", "delete"])
def test_non_admins_are_refused(monkeypatch, method):
    for name in ("set_supplement_photo", "remove_supplement_photo"):
        monkeypatch.setattr(route_mod, name, AsyncMock(side_effect=AssertionError))
    client = _client(admin=False)
    path = "/v1/admin/supplements/dsld-1/image"

    response = (
        client.post(path, files={"file": JPEG})
        if method == "post"
        else client.delete(path)
    )

    assert response.status_code == 403


def test_upload_stores_the_photo_and_returns_the_product(monkeypatch):
    store = AsyncMock(return_value=_item())
    monkeypatch.setattr(route_mod, "set_supplement_photo", store)

    response = _client().post(
        "/v1/admin/supplements/dsld-1/image", files={"file": JPEG}
    )

    assert response.status_code == 200
    assert response.json()["image_url"] == "https://img.test/bottle.jpg"
    assert response.json()["kind"] == "catalog"
    store.assert_awaited_once_with("dsld-1", JPEG[1], STORE)


def test_image_jpg_counts_as_jpeg(monkeypatch):
    monkeypatch.setattr(
        route_mod, "set_supplement_photo", AsyncMock(return_value=_item())
    )

    response = _client().post(
        "/v1/admin/supplements/dsld-1/image",
        files={"file": ("a.jpg", b"x", "image/jpg")},
    )

    assert response.status_code == 200


def test_another_file_type_is_refused_before_it_is_read(monkeypatch):
    store = AsyncMock()
    monkeypatch.setattr(route_mod, "set_supplement_photo", store)

    response = _client().post(
        "/v1/admin/supplements/dsld-1/image",
        files={"file": ("a.gif", b"GIF89a", "image/gif")},
    )

    assert response.status_code == 415
    store.assert_not_awaited()


def test_a_file_over_5_mb_is_refused(monkeypatch):
    store = AsyncMock()
    monkeypatch.setattr(route_mod, "set_supplement_photo", store)

    response = _client().post(
        "/v1/admin/supplements/dsld-1/image",
        files={"file": ("a.jpg", b"x" * (AVATAR_PHOTO_MAX_BYTES + 1), "image/jpeg")},
    )

    assert response.status_code == 413
    store.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (UnsupportedAvatarImageError("Image format is not supported"), 415),
        (AvatarImageTooLargeError("Image has too many pixels"), 413),
        (SupplementPhotoStorageError("the photo could not be saved"), 502),
        (SupplementProductNotFoundError("x"), 404),
        (SupplementNotReviewableError("approve the scan before adding a photo"), 409),
        (SupplementCatalogUnavailableError("not migrated"), 503),
    ],
)
def test_upload_errors_map_to_status_codes(monkeypatch, error, code):
    monkeypatch.setattr(route_mod, "set_supplement_photo", AsyncMock(side_effect=error))

    response = _client().post("/v1/admin/supplements/x/image", files={"file": JPEG})

    assert response.status_code == code


def test_delete_returns_the_product_without_a_photo(monkeypatch):
    remove = AsyncMock(return_value=_item(image_url=None))
    monkeypatch.setattr(route_mod, "remove_supplement_photo", remove)

    response = _client().delete("/v1/admin/supplements/dsld-1/image")

    assert response.status_code == 200
    assert response.json()["image_url"] is None
    remove.assert_awaited_once_with("dsld-1", STORE)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SupplementProductNotFoundError("x"), 404),
        (SupplementNotReviewableError("the product is rejected"), 409),
    ],
)
def test_delete_errors_map_to_status_codes(monkeypatch, error, code):
    monkeypatch.setattr(
        route_mod, "remove_supplement_photo", AsyncMock(side_effect=error)
    )

    response = _client().delete("/v1/admin/supplements/x/image")

    assert response.status_code == code


def test_the_review_item_keeps_the_label_photo_apart(monkeypatch):
    scan = replace(_item().product, label_image_url="https://img.test/label.jpg")
    monkeypatch.setattr(
        route_mod,
        "set_supplement_photo",
        AsyncMock(return_value=ReviewItem(product=scan)),
    )

    payload = (
        _client()
        .post("/v1/admin/supplements/dsld-1/image", files={"file": JPEG})
        .json()
    )

    assert payload["image_url"] == "https://img.test/bottle.jpg"
    assert payload["label_image_url"] == "https://img.test/label.jpg"
