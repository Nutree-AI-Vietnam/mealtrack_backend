"""Evolve recipe knowledge projections and pantry inventory.

Revision ID: 20260922114500000000
Revises: 20260921145751448341
Create Date: 2026-09-22 11:45:00

Ingredient rows keep source order, including repeated foods. Recipe payload
and lifecycle flags are backfilled for meals that are already servable.
Pantry inventory is a quantity, not a bought/owned flag.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922114500000000"
down_revision: str | None = "20260921145751448341"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLERGENS = (
    ("milk", "Milk"),
    ("egg", "Egg"),
    ("fish", "Fish"),
    ("shellfish", "Shellfish"),
    ("peanut", "Peanut"),
    ("tree_nut", "Tree nut"),
    ("soy", "Soy"),
    ("wheat", "Wheat"),
    ("sesame", "Sesame"),
)


def upgrade() -> None:
    _upgrade_ingredients()
    _upgrade_catalog_payload()
    _upgrade_pantry()
    _upgrade_slots_and_meals()
    _upgrade_allergens_and_aliases()


def downgrade() -> None:
    op.drop_table("food_reference_aliases")
    op.drop_table("meal_catalog_allergens")
    op.drop_table("allergen_reference")
    op.drop_column("meal", "nutrition_snapshot")
    op.drop_column("meal", "recipe_snapshot")
    op.drop_column("weekly_meal_plan_slots", "recipe_override")
    op.drop_table("weekly_grocery_item_state")
    op.drop_constraint(
        "ck_weekly_pantry_available_amount",
        "weekly_meal_plan_pantry_items",
        type_="check",
    )
    op.add_column(
        "weekly_meal_plan_pantry_items",
        sa.Column("custom_amount", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "weekly_meal_plan_pantry_items",
        sa.Column("custom_unit", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "weekly_meal_plan_pantry_items",
        sa.Column("stock_kind", sa.String(length=16), server_default="owned", nullable=False),
    )
    op.execute(
        """
        UPDATE weekly_meal_plan_pantry_items
        SET custom_amount = available_amount,
            custom_unit = available_unit,
            stock_kind = 'owned'
        """
    )
    op.create_check_constraint(
        "ck_weekly_pantry_amount",
        "weekly_meal_plan_pantry_items",
        "custom_amount IS NULL OR custom_amount >= 0",
    )
    op.create_check_constraint(
        "ck_weekly_pantry_kind",
        "weekly_meal_plan_pantry_items",
        "stock_kind IN ('bought', 'owned')",
    )
    op.drop_column("weekly_meal_plan_pantry_items", "available_unit")
    op.drop_column("weekly_meal_plan_pantry_items", "available_amount")
    op.drop_constraint(
        "ck_meal_catalog_payload_digest", "meal_catalog", type_="check"
    )
    op.drop_constraint(
        "ck_meal_catalog_nutrition_status", "meal_catalog", type_="check"
    )
    op.drop_constraint(
        "ck_meal_catalog_publication_status", "meal_catalog", type_="check"
    )
    op.drop_column("meal_catalog", "nutrition_status")
    op.drop_column("meal_catalog", "publication_status")
    op.drop_column("meal_catalog", "payload_digest")
    op.drop_column("meal_catalog", "payload_schema_version")
    op.drop_column("meal_catalog", "recipe_payload")
    op.drop_index(
        "uq_meal_catalog_ingredients_position", table_name="meal_catalog_ingredients"
    )
    op.drop_constraint(
        "ck_meal_catalog_ingredients_position",
        "meal_catalog_ingredients",
        type_="check",
    )
    op.drop_column("meal_catalog_ingredients", "notes")
    op.drop_column("meal_catalog_ingredients", "is_optional")
    op.drop_column("meal_catalog_ingredients", "raw_text")
    op.drop_column("meal_catalog_ingredients", "quantity_text")
    op.drop_constraint(
        "meal_catalog_ingredients_pkey", "meal_catalog_ingredients", type_="primary"
    )
    op.create_primary_key(
        "meal_catalog_ingredients_pkey",
        "meal_catalog_ingredients",
        ["catalog_meal_id", "food_reference_id"],
    )
    op.drop_column("meal_catalog_ingredients", "position")
    op.drop_column("meal_catalog_ingredients", "id")


def _upgrade_ingredients() -> None:
    op.add_column(
        "meal_catalog_ingredients", sa.Column("id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "meal_catalog_ingredients", sa.Column("position", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE meal_catalog_ingredients AS ingredient
        SET position = numbered.position,
            id = numbered.id
        FROM (
            SELECT catalog_meal_id,
                   food_reference_id,
                   row_number() OVER (
                       PARTITION BY catalog_meal_id
                       ORDER BY display_name, food_reference_id
                   ) AS position,
                   gen_random_uuid()::text AS id
            FROM meal_catalog_ingredients
        ) AS numbered
        WHERE ingredient.catalog_meal_id = numbered.catalog_meal_id
          AND ingredient.food_reference_id = numbered.food_reference_id
        """
    )
    op.drop_constraint(
        "meal_catalog_ingredients_pkey", "meal_catalog_ingredients", type_="primary"
    )
    op.alter_column("meal_catalog_ingredients", "id", nullable=False)
    op.alter_column("meal_catalog_ingredients", "position", nullable=False)
    op.create_primary_key(
        "meal_catalog_ingredients_pkey", "meal_catalog_ingredients", ["id"]
    )
    op.create_index(
        "uq_meal_catalog_ingredients_position",
        "meal_catalog_ingredients",
        ["catalog_meal_id", "position"],
        unique=True,
    )
    op.create_check_constraint(
        "ck_meal_catalog_ingredients_position",
        "meal_catalog_ingredients",
        "position > 0",
    )
    op.add_column(
        "meal_catalog_ingredients",
        sa.Column("quantity_text", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "meal_catalog_ingredients", sa.Column("raw_text", sa.Text(), nullable=True)
    )
    op.add_column(
        "meal_catalog_ingredients",
        sa.Column(
            "is_optional", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "meal_catalog_ingredients", sa.Column("notes", sa.Text(), nullable=True)
    )


def _upgrade_catalog_payload() -> None:
    op.add_column(
        "meal_catalog",
        sa.Column("recipe_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "meal_catalog",
        sa.Column("payload_schema_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "meal_catalog", sa.Column("payload_digest", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "meal_catalog",
        sa.Column(
            "publication_status",
            sa.String(length=16),
            server_default="published",
            nullable=False,
        ),
    )
    op.add_column(
        "meal_catalog",
        sa.Column(
            "nutrition_status",
            sa.String(length=16),
            server_default="ready",
            nullable=False,
        ),
    )
    _backfill_recipe_payloads()
    op.alter_column("meal_catalog", "recipe_payload", nullable=False)
    op.alter_column("meal_catalog", "payload_digest", nullable=False)
    op.create_check_constraint(
        "ck_meal_catalog_publication_status",
        "meal_catalog",
        "publication_status IN ('draft', 'published')",
    )
    op.create_check_constraint(
        "ck_meal_catalog_nutrition_status",
        "meal_catalog",
        "nutrition_status IN ('not_ready', 'ready')",
    )
    op.create_check_constraint(
        "ck_meal_catalog_payload_digest",
        "meal_catalog",
        "length(payload_digest) = 64",
    )


def _upgrade_pantry() -> None:
    op.add_column(
        "weekly_meal_plan_pantry_items",
        sa.Column("available_amount", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "weekly_meal_plan_pantry_items",
        sa.Column("available_unit", sa.String(length=80), nullable=True),
    )
    op.execute(
        """
        UPDATE weekly_meal_plan_pantry_items
        SET available_amount = custom_amount,
            available_unit = custom_unit
        WHERE custom_amount IS NOT NULL
        """
    )
    op.create_table(
        "weekly_grocery_item_state",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("food_reference_id", sa.Integer(), nullable=False),
        sa.Column("checked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("do_not_buy", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "manually_owned", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["food_reference_id"], ["food_reference.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["weekly_meal_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id", "food_reference_id", name="uq_weekly_grocery_state_plan_food"
        ),
    )
    op.execute(
        """
        INSERT INTO weekly_grocery_item_state (
            id, plan_id, food_reference_id, checked, do_not_buy, manually_owned,
            created_at, updated_at
        )
        SELECT gen_random_uuid()::text,
               plan_id,
               food_reference_id,
               false,
               false,
               true,
               NOW(),
               NOW()
        FROM weekly_meal_plan_pantry_items
        WHERE custom_amount IS NULL
        """
    )
    op.drop_constraint(
        "ck_weekly_pantry_kind", "weekly_meal_plan_pantry_items", type_="check"
    )
    op.drop_constraint(
        "ck_weekly_pantry_amount", "weekly_meal_plan_pantry_items", type_="check"
    )
    op.drop_column("weekly_meal_plan_pantry_items", "stock_kind")
    op.drop_column("weekly_meal_plan_pantry_items", "custom_unit")
    op.drop_column("weekly_meal_plan_pantry_items", "custom_amount")
    op.create_check_constraint(
        "ck_weekly_pantry_available_amount",
        "weekly_meal_plan_pantry_items",
        "available_amount IS NULL OR available_amount >= 0",
    )


def _upgrade_slots_and_meals() -> None:
    op.add_column(
        "weekly_meal_plan_slots",
        sa.Column("recipe_override", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "meal",
        sa.Column("recipe_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "meal",
        sa.Column(
            "nutrition_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def _upgrade_allergens_and_aliases() -> None:
    op.create_table(
        "allergen_reference",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "meal_catalog_allergens",
        sa.Column("catalog_meal_id", sa.String(length=36), nullable=False),
        sa.Column("allergen_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "source IN ('explicit', 'ingredient_derived', 'ai_derived')",
            name="ck_meal_catalog_allergen_source",
        ),
        sa.ForeignKeyConstraint(
            ["allergen_id"], ["allergen_reference.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["catalog_meal_id"], ["meal_catalog.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("catalog_meal_id", "allergen_id"),
    )
    op.create_table(
        "food_reference_aliases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("food_reference_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(
            ["food_reference_id"], ["food_reference.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias"),
    )
    allergen_table = sa.table(
        "allergen_reference",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
    )
    op.bulk_insert(
        allergen_table,
        [
            {"id": str(uuid.uuid4()), "code": code, "name": name}
            for code, name in _ALLERGENS
        ],
    )
    _backfill_allergen_links()


def _backfill_allergen_links() -> None:
    """Turn disclosures that match allergen_reference into planner links."""

    from src.domain.services.weekly_meal_planner.allergen_constraint import (
        matched_allergen_codes,
    )

    conn = op.get_bind()
    references = conn.execute(
        sa.text("SELECT id, code FROM allergen_reference")
    ).mappings()
    code_to_id = {row["code"]: row["id"] for row in references}
    meals = conn.execute(
        sa.text("SELECT id, allergens, recipe_payload FROM meal_catalog")
    ).mappings()
    for meal in meals:
        disclosures = _split_text(meal["allergens"])
        payload = meal["recipe_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            disclosures.extend(str(item) for item in payload.get("allergen_disclosures") or [])
        for code in matched_allergen_codes(disclosures, code_to_id):
            conn.execute(
                sa.text(
                    """
                    INSERT INTO meal_catalog_allergens (
                        catalog_meal_id, allergen_id, source
                    )
                    VALUES (:meal_id, :allergen_id, 'explicit')
                    ON CONFLICT (catalog_meal_id, allergen_id) DO NOTHING
                    """
                ),
                {"meal_id": meal["id"], "allergen_id": code_to_id[code]},
            )


def _backfill_recipe_payloads() -> None:
    """Keep already-active catalog meals servable and give them a payload."""

    conn = op.get_bind()
    meals = conn.execute(
        sa.text(
            """
            SELECT id, name, description, is_active, source_name, source_url,
                   equipment, allergens
            FROM meal_catalog
            """
        )
    ).mappings()
    ingredients: dict[str, list] = defaultdict(list)
    for row in conn.execute(
        sa.text(
            """
            SELECT catalog_meal_id, position, display_name, quantity, unit,
                   food_reference_id
            FROM meal_catalog_ingredients
            ORDER BY catalog_meal_id, position
            """
        )
    ).mappings():
        ingredients[row["catalog_meal_id"]].append(row)
    steps: dict[str, list] = defaultdict(list)
    for row in conn.execute(
        sa.text(
            """
            SELECT catalog_meal_id, step_number, title, description
            FROM meal_catalog_steps
            ORDER BY catalog_meal_id, step_number
            """
        )
    ).mappings():
        steps[row["catalog_meal_id"]].append(row)

    for meal in meals:
        payload = {
            "recipe_name": meal["name"],
            "description": meal["description"],
            "ingredients": [
                {
                    "name": item["display_name"],
                    "quantity": _json_quantity(item["quantity"]),
                    "unit": item["unit"],
                    "food_reference_id": item["food_reference_id"],
                }
                for item in ingredients.get(meal["id"], [])
            ],
            "instructions": [
                {
                    "step": item["step_number"],
                    "title": item["title"],
                    "instruction": item["description"],
                }
                for item in steps.get(meal["id"], [])
            ],
            "tags": [],
            "equipment": _split_text(meal["equipment"]),
            "allergen_disclosures": _split_text(meal["allergens"]),
            "source": {
                "publisher": meal["source_name"],
                "url": meal["source_url"],
            },
        }
        conn.execute(
            sa.text(
                """
                UPDATE meal_catalog
                SET recipe_payload = CAST(:payload AS jsonb),
                    payload_digest = :digest,
                    payload_schema_version = 1,
                    publication_status = :publication_status,
                    nutrition_status = 'ready'
                WHERE id = :id
                """
            ),
            {
                "id": meal["id"],
                "payload": json.dumps(payload, ensure_ascii=False),
                "digest": _payload_digest(payload),
                "publication_status": "published" if meal["is_active"] else "draft",
            },
        )


def _payload_digest(payload: dict) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_quantity(value: object) -> int | float | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    if amount == amount.to_integral():
        return int(amount)
    return float(amount)


def _split_text(value: object) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]
