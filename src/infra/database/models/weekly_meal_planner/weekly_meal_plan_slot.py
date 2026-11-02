"""Weekly meal plan slot persistence model."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin


class WeeklyMealPlanSlotORM(Base, TimestampMixin):
    """One breakfast, lunch, or dinner coordinate in a weekly plan."""

    __tablename__ = "weekly_meal_plan_slots"

    id = Column(String(36), primary_key=True)
    plan_id = Column(
        String(36),
        ForeignKey("weekly_meal_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_index = Column(Integer, nullable=False)
    slot_index = Column(Integer, nullable=False)
    catalog_meal_id = Column(
        String(36), ForeignKey("meal_catalog.id", ondelete="SET NULL"), nullable=True
    )
    is_logged = Column(Boolean, nullable=False, default=False, server_default="false")
    recipe_override = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    logged_meal_id = Column(
        String(36), ForeignKey("meal.meal_id", ondelete="SET NULL"), nullable=True
    )
    version = Column(Integer, nullable=False, default=1, server_default="1")
    # "plan": the log endpoint created the diary meal; "diary": the user linked
    # a meal they had already logged. Unlogging deletes only "plan" meals.
    log_source = Column(String(16), nullable=True)
    is_skipped = Column(Boolean, nullable=False, default=False, server_default="false")
    # A saved recipe of the user's (saved_suggestions.suggestion_id) instead of
    # a catalog recipe. No foreign key: deleting the saved recipe leaves the
    # slot as planned, and saved_recipe is the copy every read uses.
    saved_suggestion_id = Column(String(64), nullable=True)
    # none_as_null: clearing the copy must store SQL NULL, not JSON null, or
    # ck_weekly_slot_saved_recipe_snapshot refuses the row.
    saved_recipe = Column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        nullable=True,
    )

    plan = relationship("WeeklyMealPlanORM", back_populates="slots")
    catalog_meal = relationship("MealCatalogORM", lazy="joined")

    __table_args__ = (
        Index(
            "uq_weekly_meal_plan_slots_coordinate",
            "plan_id",
            "day_index",
            "slot_index",
            unique=True,
        ),
        Index("idx_weekly_meal_plan_slots_catalog", "catalog_meal_id"),
        # Meal deletes reset the slot that logged them; find it by meal id.
        Index(
            "idx_weekly_meal_plan_slots_logged_meal",
            "logged_meal_id",
            postgresql_where=text("logged_meal_id IS NOT NULL"),
        ),
        CheckConstraint("day_index BETWEEN 0 AND 6", name="ck_weekly_slot_day_index"),
        CheckConstraint("slot_index BETWEEN 0 AND 2", name="ck_weekly_slot_slot_index"),
        CheckConstraint("version > 0", name="ck_weekly_slot_version"),
        CheckConstraint(
            "log_source IS NULL OR log_source IN ('plan', 'diary')",
            name="ck_weekly_slot_log_source",
        ),
        CheckConstraint(
            "catalog_meal_id IS NULL OR saved_suggestion_id IS NULL",
            name="ck_weekly_slot_one_recipe",
        ),
        CheckConstraint(
            "(saved_suggestion_id IS NULL) = (saved_recipe IS NULL)",
            name="ck_weekly_slot_saved_recipe_snapshot",
        ),
    )
