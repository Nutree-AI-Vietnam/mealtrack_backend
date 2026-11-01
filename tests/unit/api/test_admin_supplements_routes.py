"""Admin supplement routes: auth, status codes and the response contract."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.dependencies.auth import require_admin_or_local
from src.api.routes.v1 import admin_supplements as route_mod
from src.app.services.supplement_catalog_import import (
    InvalidRecord,
    SupplementImportSummary,
)
from src.app.services.supplement_catalog_review import ReviewAction, ReviewOutcome
from src.bootstrap.supplement_catalog import SupplementCatalogUnavailableError
from src.domain.model.supplement import (
    LabelRow,
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementNutrients,
    SupplementProduct,
    SupplementProductNotFoundError,
)
from src.domain.model.supplement.catalog_review import (
    SupplementNotReviewableError,
    SupplementReviewConflictError,
)
from src.domain.ports.supplement_catalog_port import ReviewItem


def _client(admin: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(route_mod.router)

    async def deny():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin_or_local] = (
        (lambda: "admin@nutreeai.com") if admin else deny
    )
    return TestClient(app)


def _item() -> ReviewItem:
    product = SupplementProduct(
        id="scan-1",
        name="Vitamin D3",
        form=SupplementForm.SOFTGEL,
        unit_label="viên nang mềm",
        source=ProductSource.LABEL_SCAN,
        review_status=ReviewStatus.PENDING_REVIEW,
        owner_user_id="user-1",
        nutrients=SupplementNutrients({"vitamin_d": 25.0}),
        label_rows=(LabelRow(name="Vitamin D3", amount=1000, unit="IU"),),
        barcodes=("031604026165",),
        label_image_url="https://img.test/label.jpg",
    )
    return ReviewItem(product=product)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/admin/supplements/import", {"records": [{"key": "a"}]}),
        ("get", "/v1/admin/supplements/review", None),
        ("post", "/v1/admin/supplements/review/scan-1", {"action": "approve"}),
    ],
)
def test_non_admins_are_refused(monkeypatch, method, path, body):
    for name in (
        "import_supplement_catalog",
        "supplement_review_queue",
        "review_supplement",
    ):
        monkeypatch.setattr(route_mod, name, AsyncMock(side_effect=AssertionError))

    response = getattr(_client(admin=False), method)(
        path, **({"json": body} if body else {})
    )

    assert response.status_code == 403


def test_import_returns_counts_and_passes_dry_run(monkeypatch):
    summary = SupplementImportSummary(
        received=2, inserted=1, invalid=[InvalidRecord("b", "missing unit_label")]
    )
    run = AsyncMock(return_value=summary)
    monkeypatch.setattr(route_mod, "import_supplement_catalog", run)

    response = _client().post(
        "/v1/admin/supplements/import",
        json={"records": [{"key": "a"}, {"key": "b"}], "dry_run": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"] is False
    assert payload["inserted"] == 1
    assert payload["invalid"] == [{"record": "b", "error": "missing unit_label"}]
    assert run.await_args.kwargs == {"dry_run": True}


def test_review_queue_lists_items_with_their_source(monkeypatch):
    shared = SupplementProduct(
        id="shared-1",
        name="Vitamin D3 1000 IU",
        brand="Nature Made",
        form=SupplementForm.SOFTGEL,
        unit_label="viên nang mềm",
        source=ProductSource.CURATED,
        review_status=ReviewStatus.APPROVED,
        catalog_key="dsld:2959",
    )
    queue = AsyncMock(return_value=([(_item(), shared)], 1))
    monkeypatch.setattr(route_mod, "supplement_review_queue", queue)

    response = _client().get(
        "/v1/admin/supplements/review",
        params={"kind": "scan", "limit": 10, "q": " dsld:2959 "},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["kind"] == "scan"
    assert item["macro_bearing"] is False
    assert item["label_image_url"] == "https://img.test/label.jpg"
    assert item["nutrients"] == {"vitamin_d": 25.0}
    assert item["label_rows"][0]["unit"] == "IU"
    assert item["barcode_match"] == {
        "id": "shared-1",
        "name": "Vitamin D3 1000 IU",
        "brand": "Nature Made",
        "catalog_key": "dsld:2959",
        "review_status": "approved",
    }
    queue.assert_awaited_once_with(10, 0, "scan", "dsld:2959")


def test_review_passes_only_the_fields_sent(monkeypatch):
    review = AsyncMock(return_value=ReviewOutcome(item=_item(), merged_into="shared"))
    monkeypatch.setattr(route_mod, "review_supplement", review)

    response = _client().post(
        "/v1/admin/supplements/review/scan-1",
        json={"action": "approve", "changes": {"brand": None, "units_per_serving": 2}},
    )

    assert response.status_code == 200
    assert response.json()["merged_into"] == "shared"
    review.assert_awaited_once_with(
        "scan-1",
        ReviewAction.APPROVE,
        "admin@nutreeai.com",
        {"brand": None, "units_per_serving": 2.0},
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SupplementProductNotFoundError("x"), 404),
        (SupplementNotReviewableError("a Ghi nhanh estimate is never shared"), 409),
        (SupplementReviewConflictError("reload and retry"), 409),
        (ValueError("name cannot be empty"), 422),
        (SupplementCatalogUnavailableError("not migrated"), 503),
    ],
)
def test_review_errors_map_to_status_codes(monkeypatch, error, code):
    monkeypatch.setattr(route_mod, "review_supplement", AsyncMock(side_effect=error))

    response = _client().post(
        "/v1/admin/supplements/review/x", json={"action": "reject"}
    )

    assert response.status_code == code


def test_unknown_edit_fields_and_actions_are_refused(monkeypatch):
    monkeypatch.setattr(route_mod, "review_supplement", AsyncMock())
    client = _client()

    assert (
        client.post(
            "/v1/admin/supplements/review/x",
            json={"action": "approve", "changes": {"owner_user_id": None}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/admin/supplements/review/x", json={"action": "publish"}
        ).status_code
        == 422
    )


def test_import_answers_503_before_the_tables_exist(monkeypatch):
    monkeypatch.setattr(
        route_mod,
        "import_supplement_catalog",
        AsyncMock(side_effect=SupplementCatalogUnavailableError("not migrated")),
    )

    response = _client().post(
        "/v1/admin/supplements/import", json={"records": [{"key": "a"}]}
    )

    assert response.status_code == 503
