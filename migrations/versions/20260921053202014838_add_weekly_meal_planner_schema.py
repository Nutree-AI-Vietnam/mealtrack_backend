"""add weekly meal planner schema

Revision ID: 20260921053202014838
Revises: 20260917000001
Create Date: 2026-09-21 12:32:02.032511

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921053202014838"
down_revision: str | None = "20260917000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meal_catalog", sa.Column("source_name", sa.String(length=255), nullable=True)
    )
    op.add_column("meal_catalog", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column(
        "meal_catalog", sa.Column("prep_time_minutes", sa.Integer(), nullable=True)
    )
    op.add_column(
        "meal_catalog", sa.Column("cook_time_minutes", sa.Integer(), nullable=True)
    )
    op.add_column(
        "meal_catalog", sa.Column("tag", sa.String(length=160), nullable=True)
    )
    op.add_column("meal_catalog", sa.Column("allergens", sa.Text(), nullable=True))
    op.add_column("meal_catalog", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("meal_catalog", sa.Column("equipment", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_meal_catalog_prep_time_non_negative",
        "meal_catalog",
        "prep_time_minutes IS NULL OR prep_time_minutes >= 0",
    )
    op.create_check_constraint(
        "ck_meal_catalog_cook_time_non_negative",
        "meal_catalog",
        "cook_time_minutes IS NULL OR cook_time_minutes >= 0",
    )
    op.add_column(
        "meal_catalog_ingredients",
        sa.Column(
            "category", sa.String(length=32), nullable=False, server_default="pantry"
        ),
    )
    op.create_check_constraint(
        "ck_meal_catalog_ingredients_category",
        "meal_catalog_ingredients",
        "category IN ('produce', 'protein', 'pantry')",
    )
    op.create_table(
        "meal_catalog_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_meal_id", sa.String(length=36), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_meal_id"], ["meal_catalog.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("step_number > 0", name="ck_meal_catalog_step_number"),
        sa.CheckConstraint("length(title) > 0", name="ck_meal_catalog_step_title"),
        sa.CheckConstraint(
            "length(description) > 0", name="ck_meal_catalog_step_description"
        ),
    )
    op.create_index(
        "uq_meal_catalog_steps_meal_number",
        "meal_catalog_steps",
        ["catalog_meal_id", "step_number"],
        unique=True,
    )
    op.create_table(
        "weekly_meal_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="draft"
        ),
        sa.Column("people", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("preferences", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
        sa.Column("daily_calories", sa.Integer(), nullable=True),
        sa.Column("catalog_revision", sa.String(length=160), nullable=True),
        sa.Column(
            "algorithm_version",
            sa.String(length=32),
            nullable=False,
            server_default="v1",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed')", name="ck_weekly_meal_plan_status"
        ),
        sa.CheckConstraint("people BETWEEN 1 AND 6", name="ck_weekly_meal_plan_people"),
    )
    op.create_index(
        "uq_weekly_meal_plans_user_week",
        "weekly_meal_plans",
        ["user_id", "week_start_date"],
        unique=True,
    )
    op.create_index(
        "idx_weekly_meal_plans_user_status",
        "weekly_meal_plans",
        ["user_id", "status"],
    )
    op.create_table(
        "weekly_meal_plan_slots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("catalog_meal_id", sa.String(length=36), nullable=True),
        sa.Column("is_logged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("logged_meal_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["weekly_meal_plans.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["catalog_meal_id"], ["meal_catalog.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["logged_meal_id"], ["meal.meal_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "day_index BETWEEN 0 AND 6", name="ck_weekly_slot_day_index"
        ),
        sa.CheckConstraint(
            "slot_index BETWEEN 0 AND 1", name="ck_weekly_slot_slot_index"
        ),
        sa.CheckConstraint("version > 0", name="ck_weekly_slot_version"),
    )
    op.create_index(
        "uq_weekly_meal_plan_slots_coordinate",
        "weekly_meal_plan_slots",
        ["plan_id", "day_index", "slot_index"],
        unique=True,
    )
    op.create_index(
        "idx_weekly_meal_plan_slots_catalog",
        "weekly_meal_plan_slots",
        ["catalog_meal_id"],
    )
    op.create_table(
        "weekly_meal_plan_pantry_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("food_reference_id", sa.Integer(), nullable=False),
        sa.Column("custom_amount", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("stock_kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["weekly_meal_plans.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["food_reference_id"], ["food_reference.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "custom_amount IS NULL OR custom_amount >= 0",
            name="ck_weekly_pantry_amount",
        ),
        sa.CheckConstraint(
            "stock_kind IN ('bought', 'owned')", name="ck_weekly_pantry_kind"
        ),
    )
    op.create_index(
        "uq_weekly_pantry_plan_food",
        "weekly_meal_plan_pantry_items",
        ["plan_id", "food_reference_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_weekly_pantry_plan_food", table_name="weekly_meal_plan_pantry_items"
    )
    op.drop_table("weekly_meal_plan_pantry_items")
    op.drop_index(
        "idx_weekly_meal_plan_slots_catalog", table_name="weekly_meal_plan_slots"
    )
    op.drop_index(
        "uq_weekly_meal_plan_slots_coordinate", table_name="weekly_meal_plan_slots"
    )
    op.drop_table("weekly_meal_plan_slots")
    op.drop_index("idx_weekly_meal_plans_user_status", table_name="weekly_meal_plans")
    op.drop_index("uq_weekly_meal_plans_user_week", table_name="weekly_meal_plans")
    op.drop_table("weekly_meal_plans")
    op.drop_index("uq_meal_catalog_steps_meal_number", table_name="meal_catalog_steps")
    op.drop_table("meal_catalog_steps")
    op.drop_constraint(
        "ck_meal_catalog_ingredients_category",
        "meal_catalog_ingredients",
        type_="check",
    )
    op.drop_column("meal_catalog_ingredients", "category")
    op.drop_constraint(
        "ck_meal_catalog_cook_time_non_negative", "meal_catalog", type_="check"
    )
    op.drop_constraint(
        "ck_meal_catalog_prep_time_non_negative", "meal_catalog", type_="check"
    )
    for column_name in (
        "equipment",
        "summary",
        "allergens",
        "tag",
        "cook_time_minutes",
        "prep_time_minutes",
        "source_url",
        "source_name",
    ):
        op.drop_column("meal_catalog", column_name)
