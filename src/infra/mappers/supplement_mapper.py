"""Supplement rows to domain objects and back."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.domain.model.supplement import (
    LabelRow,
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementIntake,
    SupplementNutrients,
    SupplementProduct,
    UserSupplement,
)


def product_values(product: SupplementProduct) -> dict[str, Any]:
    return {
        "id": product.id,
        "catalog_key": product.catalog_key,
        "owner_user_id": product.owner_user_id,
        "brand": product.brand,
        "name": product.name,
        "name_original": product.name_original,
        "form": product.form.value,
        "unit_label": product.unit_label,
        "units_per_serving": product.units_per_serving,
        "origin_country": product.origin_country,
        "is_medicine_in_vn": product.is_medicine_in_vn,
        "sold_in_vn": product.sold_in_vn,
        "source_name": product.source_name,
        "source_url": product.source_url,
        "nutrients": product.nutrients.to_json(),
        "label_rows": [row.to_json() for row in product.label_rows],
        "macro_bearing": product.macro_bearing,
        "source": product.source.value,
        "estimated": product.estimated,
        "label_image_url": product.label_image_url,
        "review_status": product.review_status.value,
    }


def product_to_domain(row: Any, barcodes: Iterable[str]) -> SupplementProduct:
    return SupplementProduct(
        id=row.id,
        name=row.name,
        form=SupplementForm(row.form),
        unit_label=row.unit_label,
        source=ProductSource(row.source),
        review_status=ReviewStatus(row.review_status),
        owner_user_id=row.owner_user_id,
        brand=row.brand,
        name_original=row.name_original,
        units_per_serving=row.units_per_serving,
        nutrients=SupplementNutrients.from_json(row.nutrients),
        label_rows=tuple(LabelRow.from_json(r) for r in row.label_rows or []),
        barcodes=tuple(sorted(barcodes)),
        catalog_key=row.catalog_key,
        origin_country=row.origin_country,
        is_medicine_in_vn=row.is_medicine_in_vn,
        sold_in_vn=row.sold_in_vn,
        source_name=row.source_name,
        source_url=row.source_url,
        macro_bearing=row.macro_bearing,
        estimated=row.estimated,
        label_image_url=row.label_image_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_entry_to_domain(row: Any) -> UserSupplement:
    return UserSupplement(
        id=row.id,
        user_id=row.user_id,
        product_id=row.product_id,
        units_per_dose=row.units_per_dose,
        active=row.active,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def intake_values(intake: SupplementIntake) -> dict[str, Any]:
    return {
        "id": intake.id,
        "user_id": intake.user_id,
        "product_id": intake.product_id,
        "units": intake.units,
        "taken_at": intake.taken_at,
        "local_date": intake.local_date,
        "logged_on_day": intake.logged_on_day,
        "nutrients": intake.nutrients.to_json(),
        "estimated": intake.estimated,
    }


def intake_to_domain(row: Any) -> SupplementIntake:
    return SupplementIntake(
        id=row.id,
        user_id=row.user_id,
        product_id=row.product_id,
        units=row.units,
        taken_at=row.taken_at,
        local_date=row.local_date,
        logged_on_day=row.logged_on_day,
        nutrients=SupplementNutrients.from_json(row.nutrients),
        estimated=row.estimated,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )
