"""Admin review decisions on held-back catalog rows and people's label scans."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.app.services.supplement_catalog_review import (
    ReviewAction,
    review_page,
    review_product,
)
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementNutrients,
    SupplementProduct,
    SupplementProductNotFoundError,
)
from src.domain.model.supplement.catalog_review import SupplementNotReviewableError
from src.domain.ports.supplement_catalog_port import ReviewItem

OWNER = "user-1"
pytestmark = pytest.mark.asyncio


class _Catalog:
    def __init__(self, *items: ReviewItem, match: SupplementProduct | None = None):
        self.items = {item.product.id: item for item in items}
        self.match = match
        self.saved: list[tuple[SupplementProduct, str]] = []
        self.moved: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []

    async def review_item(self, product_id):
        return self.items.get(product_id)

    async def shared_with_barcode(self, barcodes, exclude_id):
        return self.match

    async def save_review(self, product, reviewer):
        self.saved.append((product, reviewer))
        return product.barcodes

    async def move_to_shared(self, user_id, from_id, to_id):
        self.moved.append((user_id, from_id, to_id))

    async def delete_product(self, product_id):
        self.deleted.append(product_id)

    async def review_queue(
        self, limit, offset, kind=None, q=None, status="pending_review"
    ):
        self.queue_status = status
        return list(self.items.values()), len(self.items)


def _scan(**overrides) -> SupplementProduct:
    fields = {
        "id": "scan-1",
        "name": "Vitamin D3 1000 IU",
        "brand": "Nature Made",
        "form": SupplementForm.SOFTGEL,
        "unit_label": "viên nang mềm",
        "source": ProductSource.LABEL_SCAN,
        "review_status": ReviewStatus.PENDING_REVIEW,
        "owner_user_id": OWNER,
        "barcodes": ("031604026165",),
        "nutrients": SupplementNutrients({"vitamin_d": 25.0}),
        "label_image_url": "https://img.test/label.jpg",
    }
    return SupplementProduct(**{**fields, **overrides})


def _held_back(**overrides) -> SupplementProduct:
    return _scan(
        id="cat-1",
        owner_user_id=None,
        source=ProductSource.CURATED,
        catalog_key="dsld:2959",
        label_image_url=None,
        **overrides,
    )


async def test_approving_a_held_back_catalog_row_shares_it():
    catalog = _Catalog(ReviewItem(_held_back(), reasons=("unit unclear",)))

    outcome = await review_product(catalog, "cat-1", ReviewAction.APPROVE, "a@x")

    saved, reviewer = catalog.saved[0]
    assert saved.review_status is ReviewStatus.APPROVED
    assert reviewer == "a@x"
    assert outcome.item.reasons == ("unit unclear",)
    assert outcome.item.reviewed_by == "a@x"
    assert outcome.merged_into is None


async def test_approving_a_scan_shares_the_scanners_row_in_place():
    catalog = _Catalog(ReviewItem(_scan()))

    outcome = await review_product(catalog, "scan-1", ReviewAction.APPROVE, "a@x")

    saved = catalog.saved[0][0]
    assert saved.id == "scan-1"
    assert saved.owner_user_id is None
    assert saved.review_status is ReviewStatus.APPROVED
    assert saved.estimated is False
    assert catalog.moved == []
    assert outcome.item.product.shared


async def test_approving_a_scan_with_a_shared_barcode_moves_the_list_there():
    shared = replace(_held_back(), id="shared-1", review_status=ReviewStatus.APPROVED)
    catalog = _Catalog(ReviewItem(_scan()), match=shared)

    outcome = await review_product(catalog, "scan-1", ReviewAction.APPROVE, "a@x")

    assert catalog.moved == [(OWNER, "scan-1", "shared-1")]
    # Nothing points at the scan any more, so it goes rather than linger in
    # the owner's own search next to the shared product.
    assert catalog.deleted == ["scan-1"]
    assert catalog.saved == []
    assert outcome.merged_into == "shared-1"
    assert outcome.item.reviewed_by == "a@x"


async def test_rejecting_a_scan_leaves_it_private_to_its_owner():
    catalog = _Catalog(ReviewItem(_scan()))

    await review_product(catalog, "scan-1", ReviewAction.REJECT, "a@x")

    saved = catalog.saved[0][0]
    assert saved.review_status is ReviewStatus.REJECTED
    assert saved.visible_to(OWNER)
    assert not saved.visible_to("someone-else")


async def test_rejecting_a_held_back_catalog_row_hides_it():
    catalog = _Catalog(ReviewItem(_held_back()))

    await review_product(catalog, "cat-1", ReviewAction.REJECT, "a@x")

    saved = catalog.saved[0][0]
    assert saved.review_status is ReviewStatus.REJECTED
    assert not saved.visible_to(OWNER)


async def test_edits_apply_before_approval_and_drop_zero_amounts():
    catalog = _Catalog(ReviewItem(_held_back()))

    await review_product(
        catalog,
        "cat-1",
        ReviewAction.APPROVE,
        "a@x",
        {"name": " Vitamin D3 25 mcg ", "nutrients": {"vitamin_d": 25.0, "zinc": 0}},
    )

    saved = catalog.saved[0][0]
    assert saved.name == "Vitamin D3 25 mcg"
    assert saved.nutrients.amounts == {"vitamin_d": 25.0}
    assert saved.review_status is ReviewStatus.APPROVED


async def test_edit_saves_changes_and_keeps_the_row_waiting():
    catalog = _Catalog(ReviewItem(_held_back()))

    await review_product(
        catalog, "cat-1", ReviewAction.EDIT, "a@x", {"units_per_serving": 2.0}
    )

    saved = catalog.saved[0][0]
    assert saved.units_per_serving == 2.0
    assert saved.review_status is ReviewStatus.PENDING_REVIEW


async def test_a_scan_whose_barcode_a_waiting_catalog_row_has_waits_for_it():
    waiting = replace(_held_back(), id="cat-2")
    catalog = _Catalog(ReviewItem(_scan()), match=waiting)

    with pytest.raises(SupplementNotReviewableError, match="dsld:2959.*decide it"):
        await review_product(catalog, "scan-1", ReviewAction.APPROVE, "a@x")
    assert (catalog.saved, catalog.moved) == ([], [])


async def test_a_catalog_row_approves_without_a_barcode_lookup():
    # A shared row's barcodes are its own: the unique index allows no other.
    other = replace(_held_back(), id="cat-2", review_status=ReviewStatus.APPROVED)
    catalog = _Catalog(ReviewItem(_held_back()), match=other)

    await review_product(catalog, "cat-1", ReviewAction.APPROVE, "a@x")

    assert catalog.saved[0][0].review_status is ReviewStatus.APPROVED
    assert catalog.moved == []


async def test_a_scan_never_merges_into_a_product_that_logs_as_food():
    shared = replace(
        _held_back(),
        id="shared-1",
        review_status=ReviewStatus.APPROVED,
        macro_bearing=True,
    )
    catalog = _Catalog(ReviewItem(_scan()), match=shared)

    with pytest.raises(SupplementNotReviewableError, match="logs as food"):
        await review_product(catalog, "scan-1", ReviewAction.APPROVE, "a@x")
    assert (catalog.saved, catalog.moved) == ([], [])


async def test_the_queue_shows_where_each_scans_barcode_already_is():
    shared = replace(_held_back(), id="shared-1", review_status=ReviewStatus.APPROVED)
    catalog = _Catalog(ReviewItem(_scan()), ReviewItem(_held_back()), match=shared)

    page, total = await review_page(catalog, 50, 0, status="pending_review")

    assert total == 2
    assert [(item.product.id, match and match.id) for item, match in page] == [
        ("scan-1", "shared-1"),
        ("cat-1", None),
    ]


async def test_an_edit_that_changes_nothing_is_refused():
    catalog = _Catalog(ReviewItem(_held_back()))

    with pytest.raises(ValueError, match="no changes"):
        await review_product(
            catalog, "cat-1", ReviewAction.EDIT, "a@x", {"units_per_serving": 1.0}
        )
    assert catalog.saved == []


async def test_an_admin_can_mark_a_catalog_row_as_food():
    catalog = _Catalog(ReviewItem(_held_back()))

    await review_product(
        catalog, "cat-1", ReviewAction.REJECT, "a@x", {"macro_bearing": True}
    )

    assert catalog.saved[0][0].macro_bearing is True


async def test_a_persons_scan_is_never_marked_as_food():
    # They could no longer log a dose of their own product.
    catalog = _Catalog(ReviewItem(_scan()))

    with pytest.raises(ValueError, match="reject it instead"):
        await review_product(
            catalog, "scan-1", ReviewAction.EDIT, "a@x", {"macro_bearing": True}
        )
    assert catalog.saved == []


async def test_a_ghi_nhanh_estimate_can_never_be_approved():
    estimate = _scan(
        source=ProductSource.DESCRIBE, estimated=True, label_image_url=None
    )
    catalog = _Catalog(ReviewItem(estimate))

    with pytest.raises(SupplementNotReviewableError):
        await review_product(catalog, "scan-1", ReviewAction.APPROVE, "a@x")
    assert catalog.saved == []


@pytest.mark.parametrize("action", [ReviewAction.APPROVE, ReviewAction.REJECT])
async def test_a_live_product_is_never_approved_or_withdrawn_again(action):
    approved = replace(_held_back(), review_status=ReviewStatus.APPROVED)
    catalog = _Catalog(ReviewItem(approved))

    with pytest.raises(SupplementNotReviewableError, match="only be edited"):
        await review_product(catalog, "cat-1", action, "a@x")
    assert catalog.saved == []


async def test_a_live_product_can_be_corrected():
    approved = replace(_held_back(), review_status=ReviewStatus.APPROVED)
    catalog = _Catalog(ReviewItem(approved))

    await review_product(
        catalog, "cat-1", ReviewAction.EDIT, "a@x", {"nutrients": {"vitamin_d": 10.0}}
    )

    saved = catalog.saved[0][0]
    assert saved.nutrients.amounts == {"vitamin_d": 10.0}
    assert saved.review_status is ReviewStatus.APPROVED


async def test_a_rejected_product_is_not_reviewed_again():
    rejected = replace(_held_back(), review_status=ReviewStatus.REJECTED)
    catalog = _Catalog(ReviewItem(rejected))

    with pytest.raises(SupplementNotReviewableError, match="rejected"):
        await review_product(
            catalog, "cat-1", ReviewAction.EDIT, "a@x", {"name": "Renamed"}
        )


async def test_an_unknown_product_is_not_found():
    with pytest.raises(SupplementProductNotFoundError):
        await review_product(_Catalog(), "nope", ReviewAction.APPROVE, "a@x")


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "  "},
        {"units_per_serving": None},
        {"nutrients": {"omega_3": 1.0}},
        {"catalog_key": "other"},
        {"label_rows": [{"name": "Zinc", "amount": 8, "nutrient": "omega_3"}]},
    ],
)
async def test_invalid_edits_are_refused(changes):
    catalog = _Catalog(ReviewItem(_held_back()))

    with pytest.raises(ValueError):
        await review_product(catalog, "cat-1", ReviewAction.EDIT, "a@x", changes)
    assert catalog.saved == []
