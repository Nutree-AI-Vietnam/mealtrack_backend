"""Explicit, owner-authorized feedback submission; never trains a model."""

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.domain.model.meal import MealStatus
from src.domain.model.meal.scan_feedback import FEEDBACK_KINDS, meal_snapshot


class ScanFeedbackService:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def submit(
        self,
        *,
        meal_id: str,
        user_id: str,
        expected_edit_count: int,
        correction_kind: str,
        consent: bool,
    ) -> dict:
        if consent is not True or correction_kind not in FEEDBACK_KINDS:
            raise ValidationException(
                "Explicit photo and correction sharing consent is required"
            )
        async with self.uow_factory() as uow:
            meal = await uow.meals.find_by_id_for_update(meal_id)
            self._check_owner(meal, user_id)
            if meal.source != "scanner" or meal.status != MealStatus.READY:
                raise ValidationException("Only completed photo scans can be shared")
            if meal.edit_count != expected_edit_count:
                raise ConflictException(
                    "The meal changed. Refresh and review it before sharing."
                )
            if not meal.image:
                raise ValidationException("The original scan photo is required")
            try:
                status = await uow.scan_feedback.submit(
                    meal_id, meal_snapshot(meal), correction_kind
                )
            except ValueError as error:
                raise ValidationException(str(error)) from error
            await uow.commit()
            return {"status": status}

    async def withdraw(self, *, meal_id: str, user_id: str) -> dict:
        async with self.uow_factory() as uow:
            meal = await uow.meals.find_by_id_for_update(meal_id)
            self._check_owner(meal, user_id)
            await uow.scan_feedback.withdraw(meal_id)
            await uow.commit()
            return {"status": "withdrawn"}

    @staticmethod
    def _check_owner(meal, user_id):
        if meal is None or meal.user_id != user_id:
            raise ResourceNotFoundException("Meal not found")
