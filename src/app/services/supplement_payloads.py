"""JSON shapes the supplement routes, replays and the activity feed share."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.domain.model.supplement import (
    SupplementIntake,
    SupplementProduct,
    UserSupplement,
)
from src.domain.utils.timezone_utils import format_iso_utc


def product_payload(product: SupplementProduct, user_id: str) -> dict[str, Any]:
    """``nutrients`` are per unit; ``mine`` marks the user's own private product.

    ``image_url`` is the catalog photo an admin set, never a label scan's photo.
    """
    return {
        "id": product.id,
        "brand": product.brand,
        "name": product.name,
        "name_original": product.name_original,
        "form": product.form.value,
        "unit_label": product.unit_label,
        "units_per_serving": product.units_per_serving,
        "nutrients": product.nutrients.to_json(),
        "label_rows": [row.to_json() for row in product.label_rows],
        "macro_bearing": product.macro_bearing,
        "estimated": product.estimated,
        "source": product.source.value,
        "review_status": product.review_status.value,
        "sold_in_vn": product.sold_in_vn,
        "image_url": product.image_url,
        "mine": product.owner_user_id == user_id,
    }


def intake_payload(intake: SupplementIntake) -> dict[str, Any]:
    """``nutrients`` is the dose's absolute snapshot taken when it was logged."""
    return {
        "id": intake.id,
        "product_id": intake.product_id,
        "units": intake.units,
        "taken_at": format_iso_utc(intake.taken_at),
        "local_date": intake.local_date.isoformat(),
        "estimated": intake.estimated,
        "nutrients": intake.nutrients.to_json(),
    }


def list_entry_payload(
    entry: UserSupplement,
    product: SupplementProduct,
    user_id: str,
    doses: Iterable[SupplementIntake] = (),
) -> dict[str, Any]:
    return {
        "id": entry.id,
        "product": product_payload(product, user_id),
        "units_per_dose": entry.units_per_dose,
        "active": entry.active,
        "sort_order": entry.sort_order,
        "doses": [intake_payload(dose) for dose in doses],
    }


def dose_activity(
    intake: SupplementIntake, product: SupplementProduct
) -> dict[str, Any]:
    """A dose in the day's activity feed, next to meals, water and movement."""
    return {
        "id": intake.id,
        "type": "supplement",
        "timestamp": format_iso_utc(intake.taken_at),
        "title": product.name,
        "brand": product.brand,
        "product_id": product.id,
        "units": intake.units,
        "unit_label": product.unit_label,
        "estimated": intake.estimated,
    }
