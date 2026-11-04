"""Catalog supplement photos on real PostgreSQL.

A photo is written outside imports and reviews, so these check that neither
ever clears it, and that the routes clean up the image store.
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import text

from src.api.base_dependencies import get_image_store
from src.api.dependencies.auth import require_admin_or_local
from src.api.routes.v1.admin_supplement_images import router as images_router
from src.api.routes.v1.admin_supplements import router as admin_router
from src.app.services.supplement_catalog_review import ReviewAction
from src.bootstrap.supplement_catalog import (
    import_supplement_catalog,
    review_supplement,
    supplement_review_queue,
)
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementProduct,
    UserSupplement,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _Store:
    """Keeps stored images in memory, like the image store port promises."""

    def __init__(self):
        self.images: dict[str, bytes] = {}

    async def save_async(self, image_bytes, content_type, image_id=None):
        self.images[image_id] = image_bytes
        return f"https://img.test/{image_id}.jpg"

    async def delete_async(self, image_id):
        return self.images.pop(image_id, None) is not None


@pytest_asyncio.fixture
async def factory(monkeypatch, pg_session, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()
    yield async_session_factory
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()


def _record(key: str, **overrides) -> dict:
    return {
        "key": key,
        "brand": "Nature Made",
        "name": f"Vitamin D3 {key}",
        "form": "softgel",
        "unit_label": "viên nang mềm",
        "units_per_serving": 1.0,
        "barcodes": [],
        "origin_country": "US",
        "sold_in_vn": True,
        "is_medicine_in_vn": False,
        "source_name": "NIH Dietary Supplement Label Database",
        "source_url": f"https://dsld.od.nih.gov/label/{key}",
        "label_rows": [{"name": "Vitamin D3", "amount": 1000, "unit": "IU"}],
        "nutrients": {"vitamin_d": 25.0},
        **overrides,
    }


def _jpeg() -> tuple[str, bytes, str]:
    output = BytesIO()
    Image.new("RGB", (40, 60), (20, 120, 60)).save(output, format="JPEG")
    return ("bottle.jpg", output.getvalue(), "image/jpeg")


def _client(store: _Store) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(images_router)
    app.dependency_overrides[require_admin_or_local] = lambda: "admin@nutreeai.com"
    app.dependency_overrides[get_image_store] = lambda: store
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _user(session) -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"{uuid4()}@example.com",
        username=f"user-{uuid4()}",
        password_hash="unused",
    )
    session.add(user)
    await session.commit()
    return user.id


async def _updated_at():
    async with AsyncUnitOfWork() as uow:
        return await uow.session.scalar(
            text("SELECT updated_at FROM supplement_products")
        )


async def _catalog_id(key: str) -> str:
    items, _ = await supplement_review_queue(10, 0, q=key, status="approved")
    return items[0][0].product.id


async def test_a_photo_outlives_reimports_and_edits(factory, pg_session):
    reader = await _user(pg_session)
    store = _Store()
    await import_supplement_catalog([_record("1"), _record("2")])
    product_id = await _catalog_id("1")

    async with _client(store) as client:
        first = await client.post(
            f"/v1/admin/supplements/{product_id}/image", files={"file": _jpeg()}
        )
        second = await client.post(
            f"/v1/admin/supplements/{product_id}/image", files={"file": _jpeg()}
        )
        missing = await client.get(
            "/v1/admin/supplements/review",
            params={"status": "approved", "has_image": "false"},
        )

    assert first.status_code == second.status_code == 200
    url = second.json()["image_url"]
    assert url != first.json()["image_url"]
    # The replaced photo is gone from the store; only the current one is kept.
    assert [f"https://img.test/{key}.jpg" for key in store.images] == [url]
    assert [item["catalog_key"] for item in missing.json()["items"]] == ["2"]

    changed = await import_supplement_catalog([_record("1", name="Vitamin D3 new")])
    await review_supplement(
        product_id, ReviewAction.EDIT, "admin@x", {"units_per_serving": 2}
    )

    assert changed.updated == 1
    async with AsyncUnitOfWork() as uow:
        [found] = await uow.supplements.search_products("vitamin d3 new", reader)
    assert found.image_url == url


async def test_removing_a_photo_clears_the_row_and_the_store(factory):
    store = _Store()
    await import_supplement_catalog([_record("1")])
    product_id = await _catalog_id("1")
    before = await _updated_at()

    async with _client(store) as client:
        added = await client.post(
            f"/v1/admin/supplements/{product_id}/image", files={"file": _jpeg()}
        )
        removed = await client.delete(f"/v1/admin/supplements/{product_id}/image")

    # A photo is not a content change: the admin card keys on updated_at.
    assert added.json()["updated_at"] == removed.json()["updated_at"]
    assert await _updated_at() == before
    assert removed.status_code == 200
    assert removed.json()["image_url"] is None
    assert store.images == {}
    async with AsyncUnitOfWork() as uow:
        rows = await uow.session.execute(
            text("SELECT image_id, image_url FROM supplement_products")
        )
    assert rows.all() == [(None, None)]


async def test_a_waiting_scan_takes_no_photo_and_leaves_none_stored(
    factory, pg_session
):
    owner = await _user(pg_session)
    scan = SupplementProduct(
        id=str(uuid4()),
        name="Vitamin D3 1000 IU",
        form=SupplementForm.SOFTGEL,
        unit_label="viên nang mềm",
        source=ProductSource.LABEL_SCAN,
        review_status=ReviewStatus.PENDING_REVIEW,
        owner_user_id=owner,
    )
    async with AsyncUnitOfWork() as uow:
        await uow.supplements.add_product(scan)
        await uow.supplements.save_list_entry(
            UserSupplement(
                id=str(uuid4()), user_id=owner, product_id=scan.id, units_per_dose=1
            )
        )
        await uow.commit()
    store = _Store()

    async with _client(store) as client:
        refused = await client.post(
            f"/v1/admin/supplements/{scan.id}/image", files={"file": _jpeg()}
        )

    assert refused.status_code == 409
    assert store.images == {}
