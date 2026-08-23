"""Backfill popular staple Vietnamese names and serving labels.

Revision ID: 20260823000002
Revises: 20260823000001
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "20260823000002"
down_revision: str | None = "20260823000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# beef=205, pork=294, white rice=348, egg=363, whole milk=440
_STAPLE_NAME_VI = {
    348: "Cơm",
}

_STAPLE_SERVING_NAME_VI = (
    (205, "serving", "Khẩu phần"),
    (205, "cup, cooked, shredded", "Cốc, đã nấu chín, xé sợi"),
    (205, "oz, boneless, cooked", "Oz, không xương, đã nấu"),
    (205, "serving (85g)", "Khẩu phần"),
    (205, "cup, cooked, diced", "Cốc, đã nấu, cắt hạt lựu"),
    (
        205,
        "oz, boneless, raw (yield after cooking)",
        "Oz, không xương, sống (sau khi nấu)",
    ),
    (205, "cubic inch, boneless, cooked", "Inch khối, không xương, đã nấu"),
    (294, "serving", "Khẩu phần"),
    (294, "cup, cooked, diced", "Cốc, đã nấu, cắt hạt lựu"),
    (294, "oz, boneless, cooked", "Oz, không xương, đã nấu"),
    (294, "serving (85g)", "Khẩu phần"),
    (
        294,
        "oz, boneless, raw (yield after cooking)",
        "Oz, không xương, sống (sau khi nấu)",
    ),
    (294, "cubic inch, boneless, cooked", "Inch khối, không xương, đã nấu"),
    (
        294,
        "oz, with bone, cooked (yield after bone removed)",
        "Oz, có xương, đã nấu (sau khi bỏ xương)",
    ),
    (348, "cup, cooked", "Cốc, đã nấu"),
    (348, "serving", "Khẩu phần"),
    (348, "serving (105g)", "Khẩu phần"),
    (348, "cup, dry, yields", "Cốc, khô, cho ra"),
    (348, "oz, dry, yields", "Oz, khô, cho ra"),
    (363, "large", "Lớn"),
    (363, "medium", "Vừa"),
    (363, "serving", "Khẩu phần"),
    (363, "small", "Nhỏ"),
    (363, "extra large", "Cực lớn"),
    (363, "jumbo", "Khổng lồ"),
    (440, "cup", "Cốc"),
    (440, "ml", "Ml"),
    (440, "serving", "Khẩu phần"),
    (440, "fl oz", "Oz lỏng"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for ref_id, name_vi in _STAPLE_NAME_VI.items():
        conn.execute(
            text(
                """
                UPDATE food_reference
                SET name_vi = :name_vi
                WHERE id = :ref_id
                  AND (name_vi IS NULL OR btrim(name_vi) = '')
                """
            ),
            {"ref_id": ref_id, "name_vi": name_vi},
        )

    for ref_id, name, name_vi in _STAPLE_SERVING_NAME_VI:
        conn.execute(
            text(
                """
                UPDATE food_reference_serving_sizes
                SET name_vi = :name_vi
                WHERE food_reference_id = :ref_id
                  AND name = :name
                  AND (name_vi IS NULL OR btrim(name_vi) = '')
                """
            ),
            {"ref_id": ref_id, "name": name, "name_vi": name_vi},
        )

    conn.execute(
        text(
            """
            UPDATE food_reference_serving_sizes
            SET description = '100 ml'
            WHERE food_reference_id = 440
              AND name = 'ml'
              AND (
                description IS NULL
                OR lower(description) IN ('ml', '1 ml')
              )
            """
        )
    )


def downgrade() -> None:
    # Data backfill only — leave labels in place.
    pass
