"""Catalog supplement photos: re-encoding, which rows take one, and cleanup."""

from __future__ import annotations

import logging
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from src.app.services.supplement_product_photo import (
    StoredPhoto,
    SupplementPhotoStorageError,
    delete_photo,
    point_at_photo,
    store_photo,
)
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementProduct,
    SupplementProductNotFoundError,
)
from src.domain.model.supplement.catalog_review import SupplementNotReviewableError
from src.domain.ports.supplement_catalog_port import ReviewItem
from src.domain.utils.avatar_image import UnsupportedAvatarImageError

pytestmark = pytest.mark.asyncio
PHOTO = StoredPhoto("new-id", "https://img.test/new.jpg")


class _Catalog:
    def __init__(self, item: ReviewItem | None):
        self.item = item
        self.writes: list[tuple[str, str | None, str | None]] = []

    async def review_item(self, product_id):
        return self.item

    async def set_image(self, product_id, image_id, image_url):
        self.writes.append((product_id, image_id, image_url))


def _product(**overrides) -> SupplementProduct:
    values = {
        "id": "p1",
        "name": "Vitamin D3",
        "form": SupplementForm.SOFTGEL,
        "unit_label": "viên nang mềm",
        "source": ProductSource.CURATED,
        "review_status": ReviewStatus.APPROVED,
        "catalog_key": "dsld:1",
    }
    values.update(overrides)
    return SupplementProduct(**values)


def _png(size=(2400, 1200)) -> bytes:
    output = BytesIO()
    Image.new("RGBA", size, (200, 30, 30, 0)).save(output, format="PNG")
    return output.getvalue()


async def test_an_upload_is_stored_as_a_small_jpeg_under_a_new_id():
    store = AsyncMock()
    store.save_async.return_value = "https://img.test/x.jpg"

    photo = await store_photo(store, _png())

    data, content_type, image_id = store.save_async.await_args.args
    assert content_type == "image/jpeg"
    assert photo == StoredPhoto(image_id, "https://img.test/x.jpg")
    with Image.open(BytesIO(data)) as stored:
        assert stored.format == "JPEG"
        assert max(stored.size) == 1024


async def test_a_file_that_is_not_an_image_is_refused_before_storing():
    store = AsyncMock()

    with pytest.raises(UnsupportedAvatarImageError):
        await store_photo(store, b"not an image")

    store.save_async.assert_not_awaited()


async def test_a_store_failure_becomes_a_storage_error():
    store = AsyncMock()
    store.save_async.side_effect = RuntimeError("cloudflare down")

    with pytest.raises(SupplementPhotoStorageError):
        await store_photo(store, _png((10, 10)))


@pytest.mark.parametrize(
    "product",
    [
        _product(),
        _product(review_status=ReviewStatus.PENDING_REVIEW),
    ],
    ids=["live", "waiting"],
)
async def test_a_catalog_row_takes_a_photo_and_returns_the_old_one(product):
    catalog = _Catalog(
        ReviewItem(
            product=_product(
                review_status=product.review_status,
                image_id="old-id",
                image_url="https://img.test/old.jpg",
            )
        )
    )

    item, previous = await point_at_photo(catalog, "p1", PHOTO)

    assert catalog.writes == [("p1", "new-id", "https://img.test/new.jpg")]
    assert item.product.image_url == "https://img.test/new.jpg"
    assert previous == "old-id"


async def test_removing_a_photo_clears_it():
    catalog = _Catalog(ReviewItem(product=_product(image_id="old-id", image_url="u")))

    item, previous = await point_at_photo(catalog, "p1", None)

    assert catalog.writes == [("p1", None, None)]
    assert item.product.image_url is None
    assert previous == "old-id"


async def test_removing_when_there_is_no_photo_writes_nothing():
    catalog = _Catalog(ReviewItem(product=_product()))

    item, previous = await point_at_photo(catalog, "p1", None)

    assert catalog.writes == []
    assert previous is None


@pytest.mark.parametrize(
    "product",
    [
        _product(
            source=ProductSource.LABEL_SCAN,
            review_status=ReviewStatus.PENDING_REVIEW,
            owner_user_id="user-1",
            catalog_key=None,
        ),
        _product(review_status=ReviewStatus.REJECTED),
        _product(
            source=ProductSource.LABEL_SCAN,
            review_status=ReviewStatus.PRIVATE,
            owner_user_id="user-1",
            catalog_key=None,
        ),
    ],
    ids=["waiting scan", "rejected", "private"],
)
async def test_rows_outside_the_catalog_take_no_photo(product):
    catalog = _Catalog(ReviewItem(product=product))

    with pytest.raises(SupplementNotReviewableError):
        await point_at_photo(catalog, "p1", PHOTO)

    assert catalog.writes == []


async def test_an_unknown_product_is_not_found():
    with pytest.raises(SupplementProductNotFoundError):
        await point_at_photo(_Catalog(None), "missing", PHOTO)


async def test_a_failed_delete_is_logged_for_removal_by_hand(caplog):
    store = AsyncMock()
    store.delete_async.side_effect = RuntimeError("timeout")

    with caplog.at_level(logging.ERROR):
        await delete_photo(store, "old-id", reason="photo replaced")

    assert "image_id=old-id" in caplog.text
    assert "reason=photo replaced" in caplog.text
