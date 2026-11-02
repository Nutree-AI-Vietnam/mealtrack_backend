"""Supplement products and their barcodes."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin
from src.infra.database.supplement_search_name_sql import SupplementSearchName

_JSON = JSON().with_variant(JSONB(), "postgresql")


class SupplementProductORM(Base, BaseMixin):
    """A shared catalog row (no owner) or a person's own product.

    ``nutrients`` holds tracked keys per unit in canonical units plus
    ``vitamin_a_preformed``; ``label_rows`` keeps every printed line.
    """

    __tablename__ = "supplement_products"

    catalog_key = Column(String(128), nullable=True, unique=True)
    owner_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    brand = Column(String(255), nullable=True)
    name = Column(String(255), nullable=False)
    name_original = Column(String(255), nullable=True)
    form = Column(String(16), nullable=False)
    unit_label = Column(String(32), nullable=False)
    units_per_serving = Column(Float, nullable=False, default=1.0)
    origin_country = Column(String(8), nullable=True)
    is_medicine_in_vn = Column(Boolean, nullable=False, default=False)
    sold_in_vn = Column(Boolean, nullable=False, default=False)
    source_name = Column(String(255), nullable=True)
    source_url = Column(String(1024), nullable=True)
    nutrients = Column(_JSON, nullable=False, default=dict)
    label_rows = Column(_JSON, nullable=False, default=list)
    macro_bearing = Column(Boolean, nullable=False, default=False)
    source = Column(String(16), nullable=False)
    estimated = Column(Boolean, nullable=False, default=False)
    label_image_url = Column(String(512), nullable=True)
    review_status = Column(String(16), nullable=False)
    # Accent-folded brand and names, so "dau ca" finds "Dầu cá" (trigram index).
    name_search = Column(Text, Computed(SupplementSearchName(), persisted=True))

    __table_args__ = (
        CheckConstraint(
            "form IN ('capsule', 'softgel', 'tablet', 'chewable', 'gummy',"
            " 'effervescent', 'powder', 'liquid', 'drops', 'sachet')",
            name="ck_supplement_products_form",
        ),
        CheckConstraint(
            "source IN ('curated', 'label_scan', 'barcode', 'describe')",
            name="ck_supplement_products_source",
        ),
        CheckConstraint(
            "review_status IN ('private', 'pending_review', 'approved', 'rejected')",
            name="ck_supplement_products_review_status",
        ),
        CheckConstraint(
            "units_per_serving > 0", name="ck_supplement_products_units_per_serving"
        ),
        # Approved rows are shared; private rows belong to someone.
        CheckConstraint(
            "review_status <> 'approved' OR owner_user_id IS NULL",
            name="ck_supplement_products_approved_shared",
        ),
        CheckConstraint(
            "review_status <> 'private' OR owner_user_id IS NOT NULL",
            name="ck_supplement_products_private_owned",
        ),
        # A Ghi nhanh estimate stays its owner's and never enters the catalog.
        CheckConstraint(
            "NOT estimated OR (source = 'describe' AND owner_user_id IS NOT NULL)",
            name="ck_supplement_products_estimate_private",
        ),
        Index(
            "ix_supplement_products_name_search_trgm",
            "name_search",
            postgresql_using="gin",
            postgresql_ops={"name_search": "gin_trgm_ops"},
        ),
    )


class SupplementProductBarcodeORM(Base):
    """GTIN-14 barcodes; bottle sizes of one product share its row.

    ``owner_user_id`` mirrors the product's, so a barcode maps to at most one
    shared product and one product per owner.
    """

    __tablename__ = "supplement_product_barcodes"

    product_id = Column(
        String(36),
        ForeignKey("supplement_products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    barcode = Column(String(14), primary_key=True, index=True)
    owner_user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "length(barcode) = 14", name="ck_supplement_product_barcodes_gtin14"
        ),
        Index(
            "uq_supplement_product_barcodes_shared",
            "barcode",
            unique=True,
            postgresql_where=text("owner_user_id IS NULL"),
        ),
        Index(
            "uq_supplement_product_barcodes_owned",
            "owner_user_id",
            "barcode",
            unique=True,
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
    )
