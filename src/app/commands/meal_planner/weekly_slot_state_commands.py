"""Weekly plan slot state changes that do not create a diary meal."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class UnlogMealPlanSlotCommand(Command):
    """Release a logged slot; delete its diary meal only if the plan made it."""

    user_id: str
    plan_id: str
    slot_id: str
    idempotency_key: str


@dataclass
class LinkMealPlanSlotCommand(Command):
    """Mark a slot logged with a meal the user already has in the diary."""

    user_id: str
    plan_id: str
    slot_id: str
    idempotency_key: str
    meal_id: str
    # Header timezone; the user's saved timezone applies when it is absent.
    timezone: str | None = None


@dataclass
class SetMealPlanSlotSkipCommand(Command):
    """Skip a slot (or undo the skip); skipped slots buy no groceries."""

    user_id: str
    plan_id: str
    slot_id: str
    idempotency_key: str
    skipped: bool
