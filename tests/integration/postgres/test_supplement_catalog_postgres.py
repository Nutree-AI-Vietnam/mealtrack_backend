"""Curated catalog import and admin review on real PostgreSQL.

Upserts by key, barcode claims and the in-place promotion of a scan depend
on unique indexes and constraints, so they are checked against the database.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

from src.api.dependencies.auth import require_admin_or_local
from src.api.routes.v1.admin_supplements import router as admin_supplements_router
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
    SupplementNutrients,
    SupplementProduct,
    UserSupplement,
)
from src.domain.model.supplement.catalog_review import SupplementNotReviewableError
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

UPC = "031604026165"
GTIN = "00031604026165"


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
        "source_name": "NIH Dietary Supplement Label Database",
        "source_url": f"https://dsld.od.nih.gov/label/{key}",
        "label_rows": [{"name": "Vitamin D3", "amount": 1000, "unit": "IU"}],
        "nutrients": {"vitamin_d": 25.0},
        **overrides,
    }


async def _scan(owner: str, **overrides) -> SupplementProduct:
    fields = {
        "id": str(uuid4()),
        "name": "Vitamin D3 1000 IU",
        "brand": "Nature Made",
        "form": SupplementForm.SOFTGEL,
        "unit_label": "viên nang mềm",
        "source": ProductSource.LABEL_SCAN,
        "review_status": ReviewStatus.PENDING_REVIEW,
        "owner_user_id": owner,
        "barcodes": (UPC,),
        "nutrients": SupplementNutrients({"vitamin_d": 25.0}),
        "label_image_url": "https://img.test/label.jpg",
    }
    product = SupplementProduct(**{**fields, **overrides})
    async with AsyncUnitOfWork() as uow:
        await uow.supplements.add_product(product)
        await uow.supplements.save_list_entry(
            UserSupplement(
                id=str(uuid4()), user_id=owner, product_id=product.id, units_per_dose=1
            )
        )
        await uow.commit()
    return product


async def _rows(sql: str, **params):
    async with AsyncUnitOfWork() as uow:
        return (await uow.session.execute(text(sql), params)).all()


async def test_import_is_idempotent_and_holds_back_records_with_issues(factory):
    records = [
        _record("1", barcodes=[UPC]),
        _record("2", barcodes=[UPC]),
        _record("3", issues=["unit unclear"]),
    ]

    first = await import_supplement_catalog(records)
    second = await import_supplement_catalog(records)

    assert (first.inserted, first.held_back) == (3, 1)
    assert [(s.catalog_key, s.barcode, s.reason) for s in first.barcode_skips] == [
        ("2", GTIN, "on 1")
    ]
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 3)
    assert len(second.barcode_skips) == 1
    rows = await _rows(
        "SELECT catalog_key, review_status, review_reasons FROM supplement_products"
        " ORDER BY catalog_key"
    )
    assert rows == [
        ("1", "approved", None),
        ("2", "approved", None),
        ("3", "pending_review", ["unit unclear"]),
    ]
    # No reasons is SQL NULL, so "IS NULL" finds every approved row.
    assert await _rows(
        "SELECT count(*) FROM supplement_products WHERE review_reasons IS NULL"
    ) == [(2,)]


async def test_reimport_updates_in_place_and_frees_dropped_barcodes(factory):
    await import_supplement_catalog([_record("1", barcodes=[UPC]), _record("2")])
    [(first_id,)] = await _rows(
        "SELECT id FROM supplement_products WHERE catalog_key = '1'"
    )

    summary = await import_supplement_catalog(
        [
            _record("1", name="Vitamin D3 renamed", nutrients={"vitamin_d": 50.0}),
            _record("2", barcodes=[UPC]),
        ]
    )

    assert (summary.updated, summary.barcode_skips) == (2, [])
    rows = await _rows(
        "SELECT p.catalog_key, p.id, p.name, b.barcode FROM supplement_products p"
        " LEFT JOIN supplement_product_barcodes b ON b.product_id = p.id"
        " ORDER BY p.catalog_key"
    )
    assert [(key, name, code) for key, _, name, code in rows] == [
        ("1", "Vitamin D3 renamed", None),
        ("2", "Vitamin D3 2", GTIN),
    ]
    assert rows[0][1] == first_id


async def test_reimport_leaves_rows_an_admin_reviewed(factory):
    await import_supplement_catalog([_record("3", issues=["unit unclear"])])
    [(product_id,)] = await _rows("SELECT id FROM supplement_products")
    await review_supplement(product_id, ReviewAction.APPROVE, "admin@x")

    summary = await import_supplement_catalog([_record("3", issues=["still unclear"])])

    assert summary.kept_reviewed == 1
    assert await _rows(
        "SELECT review_status, reviewed_by FROM supplement_products"
    ) == [("approved", "admin@x")]


async def test_reimport_never_takes_an_approved_row_out(factory):
    await import_supplement_catalog([_record("1")])

    summary = await import_supplement_catalog(
        [_record("1", name="Changed", issues=["new doubt"])]
    )

    assert (summary.kept_approved, summary.updated) == (1, 0)
    assert await _rows("SELECT name, review_status FROM supplement_products") == [
        ("Vitamin D3 1", "approved")
    ]


async def test_a_scan_waits_for_the_catalog_row_holding_its_barcode(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    await import_supplement_catalog([_record("3", barcodes=[UPC], issues=["unclear"])])
    [(held_id,)] = await _rows("SELECT id FROM supplement_products")
    scan = await _scan(alice)

    with pytest.raises(SupplementNotReviewableError, match="review it first"):
        await review_supplement(scan.id, ReviewAction.APPROVE, "admin@x")
    await review_supplement(held_id, ReviewAction.REJECT, "admin@x")
    outcome = await review_supplement(scan.id, ReviewAction.APPROVE, "admin@x")

    assert outcome.item.product.barcodes == (GTIN,)
    async with AsyncUnitOfWork() as uow:
        assert (await uow.supplements.find_by_barcode(UPC, bob)).id == scan.id


async def test_a_rejected_catalog_row_gives_up_its_barcodes(factory):
    await import_supplement_catalog([_record("3", barcodes=[UPC], issues=["unclear"])])
    [(held_id,)] = await _rows("SELECT id FROM supplement_products")

    outcome = await review_supplement(held_id, ReviewAction.REJECT, "admin@x")
    later = await import_supplement_catalog([_record("4", barcodes=[UPC])])

    assert outcome.item.product.barcodes == ()
    assert later.barcode_skips == []
    assert await _rows(
        "SELECT p.catalog_key FROM supplement_product_barcodes b"
        " JOIN supplement_products p ON p.id = b.product_id"
    ) == [("4",)]


async def test_approving_a_scan_shares_it_in_place(factory, pg_session):
    alice, bob = await _user(pg_session), await _user(pg_session)
    scan = await _scan(alice)

    queue, total = await supplement_review_queue(10, 0, "scan")
    assert (total, [item.product.id for item in queue]) == (1, [scan.id])
    outcome = await review_supplement(scan.id, ReviewAction.APPROVE, "admin@x")

    assert outcome.merged_into is None
    assert outcome.item.product.barcodes == (GTIN,)
    async with AsyncUnitOfWork() as uow:
        found = await uow.supplements.find_by_barcode(UPC, bob)
        mine = await uow.supplements.list_supplements(alice)
    assert found.id == scan.id
    assert found.review_status is ReviewStatus.APPROVED
    assert [entry.product_id for entry in mine] == [scan.id]
    assert await _rows("SELECT owner_user_id FROM supplement_product_barcodes") == [
        (None,)
    ]


async def test_approving_a_scan_of_a_shared_barcode_moves_the_list(factory, pg_session):
    alice, carol = await _user(pg_session), await _user(pg_session)
    await import_supplement_catalog([_record("1", barcodes=[UPC])])
    [(shared_id,)] = await _rows("SELECT id FROM supplement_products")
    alices, carols = await _scan(alice), await _scan(carol)
    async with AsyncUnitOfWork() as uow:
        await uow.supplements.save_list_entry(
            UserSupplement(
                id=str(uuid4()), user_id=carol, product_id=shared_id, units_per_dose=2
            )
        )
        await uow.commit()

    outcome = await review_supplement(alices.id, ReviewAction.APPROVE, "admin@x")
    await review_supplement(carols.id, ReviewAction.APPROVE, "admin@x")

    assert outcome.merged_into == shared_id
    async with AsyncUnitOfWork() as uow:
        alice_list = await uow.supplements.list_supplements(alice)
        carol_list = await uow.supplements.list_supplements(carol)
        alice_scan = await uow.supplements.get_product(alices.id, alice)
    assert [e.product_id for e in alice_list] == [shared_id]
    # Carol already had the shared product listed; her duplicate row goes.
    assert [(e.product_id, e.units_per_dose) for e in carol_list] == [(shared_id, 2)]
    assert alice_scan.review_status is ReviewStatus.PRIVATE


async def test_rejecting_hides_catalog_rows_and_keeps_scans_private(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    await import_supplement_catalog([_record("3", issues=["unit unclear"])])
    [(held_id,)] = await _rows("SELECT id FROM supplement_products")
    scan = await _scan(alice, barcodes=())

    await review_supplement(held_id, ReviewAction.REJECT, "admin@x")
    await review_supplement(scan.id, ReviewAction.REJECT, "admin@x")

    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.search_products("vitamin d3", bob) == []
        mine = await uow.supplements.get_product(scan.id, alice)
    assert mine.review_status is ReviewStatus.REJECTED
    assert await supplement_review_queue(10, 0) == ([], 0)


async def test_a_ghi_nhanh_estimate_is_never_approved(factory, pg_session):
    alice = await _user(pg_session)
    estimate = await _scan(
        alice,
        source=ProductSource.DESCRIBE,
        estimated=True,
        review_status=ReviewStatus.PRIVATE,
        barcodes=(),
        label_image_url=None,
    )

    with pytest.raises(SupplementNotReviewableError):
        await review_supplement(estimate.id, ReviewAction.APPROVE, "admin@x")

    assert await _rows(
        "SELECT owner_user_id, review_status FROM supplement_products"
    ) == [(alice, "private")]


async def test_admin_routes_import_list_and_approve_over_http(factory, pg_session):
    bob = await _user(pg_session)
    app = FastAPI()
    app.include_router(admin_supplements_router)
    app.dependency_overrides[require_admin_or_local] = lambda: "admin@nutreeai.com"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/v1/admin/supplements/import",
            json={"records": [_record("1"), _record("3", issues=["unit unclear"])]},
        )
        queue = await client.get("/v1/admin/supplements/review")
        held_id = queue.json()["items"][0]["id"]
        approved = await client.post(
            f"/v1/admin/supplements/review/{held_id}",
            json={"action": "approve", "changes": {"units_per_serving": 2}},
        )
        again = await client.post(
            f"/v1/admin/supplements/review/{held_id}", json={"action": "reject"}
        )
        empty = await client.get("/v1/admin/supplements/review")

    assert imported.status_code == 200
    assert (imported.json()["inserted"], imported.json()["held_back"]) == (2, 1)
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["reasons"] == ["unit unclear"]
    assert approved.status_code == 200
    item = approved.json()["item"]
    assert (item["review_status"], item["units_per_serving"]) == ("approved", 2.0)
    assert item["reviewed_by"] == "admin@nutreeai.com"
    assert again.status_code == 409
    assert empty.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    async with AsyncUnitOfWork() as uow:
        found = await uow.supplements.search_products("vitamin d3", bob)
    assert len(found) == 2
