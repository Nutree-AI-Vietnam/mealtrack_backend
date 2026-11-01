"""A read supplement label becomes the person's own product, or is routed to food."""

from __future__ import annotations

from src.domain.model.ai.supplement_label_contract import SupplementLabelResponse
from src.domain.model.supplement.label_units import nutrients_from_label
from src.domain.model.supplement.supplement_product import (
    LabelRow,
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementProduct,
)

FOOD_KINDS = frozenset({"protein_powder", "mass_gainer", "meal_replacement"})
# Backstop when the model calls a shake a supplement: this much per unit is food.
# Fish oil (about 10 kcal a softgel) and gummies (about 10 kcal each) stay below it.
FOOD_PROTEIN_G_PER_UNIT = 10.0
FOOD_KCAL_PER_UNIT = 50.0
# Shakes and powders are read per ml or per gram, so a serving counts too:
# a cod liver oil spoon (about 40 kcal) stays a supplement, a shake does not.
SERVED_FORMS = frozenset({"powder", "liquid", "sachet"})
FOOD_PROTEIN_G_PER_SERVING = 10.0
FOOD_KCAL_PER_SERVING = 100.0

UNIT_LABELS = {
    SupplementForm.CAPSULE: "viên nang",
    SupplementForm.SOFTGEL: "viên nang mềm",
    SupplementForm.TABLET: "viên",
    SupplementForm.CHEWABLE: "viên nhai",
    SupplementForm.GUMMY: "viên kẹo dẻo",
    SupplementForm.EFFERVESCENT: "viên sủi",
    SupplementForm.POWDER: "muỗng",
    SupplementForm.LIQUID: "ml",
    SupplementForm.DROPS: "giọt",
    SupplementForm.SACHET: "gói",
}


def is_food(read: SupplementLabelResponse) -> bool:
    """Protein powders, gainers and meal shakes log as food, by kind or by numbers."""
    if read.product_kind in FOOD_KINDS:
        return True
    macros = read.macros_per_serving
    if macros is None:
        return False
    protein, kcal = macros.protein_g or 0, macros.calories_kcal or 0
    if read.form in SERVED_FORMS and (
        protein >= FOOD_PROTEIN_G_PER_SERVING or kcal >= FOOD_KCAL_PER_SERVING
    ):
        return True
    per_unit = 1 / read.serving_units
    return (
        protein * per_unit >= FOOD_PROTEIN_G_PER_UNIT
        or kcal * per_unit >= FOOD_KCAL_PER_UNIT
    )


def product_from_label(
    read: SupplementLabelResponse,
    *,
    product_id: str,
    owner_user_id: str,
    label_image_url: str | None,
) -> SupplementProduct:
    """The scanner's own product, per unit; offered for review when it has a brand
    and at least one tracked nutrient, private otherwise."""
    rows = tuple(
        LabelRow(row.name, row.amount, row.unit, row.nutrient) for row in read.rows
    )
    nutrients = nutrients_from_label(rows, read.serving_units)
    form = SupplementForm(read.form)
    reviewable = bool(read.brand) and bool(nutrients.amounts)
    return SupplementProduct(
        id=product_id,
        name=read.product_name,
        brand=read.brand,
        form=form,
        unit_label=UNIT_LABELS[form],
        source=ProductSource.LABEL_SCAN,
        review_status=(
            ReviewStatus.PENDING_REVIEW if reviewable else ReviewStatus.PRIVATE
        ),
        owner_user_id=owner_user_id,
        units_per_serving=read.serving_units,
        nutrients=nutrients,
        label_rows=rows,
        barcodes=(read.barcode,) if read.barcode else (),
        label_image_url=label_image_url,
    )
