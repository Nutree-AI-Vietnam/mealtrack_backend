"""Feedback persistence; callers own the meal lock and transaction."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.meal import Meal, MealStatus
from src.domain.model.meal.scan_feedback import (
    CONSENT_VERSION,
    original_scan_snapshot,
    snapshot_hash,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.meal.scan_feedback import ScanFeedbackORM


class ScanFeedbackRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def capture_baseline(self, meal: Meal) -> None:
        if (
            meal.source != "scanner"
            or meal.status != MealStatus.READY
            or meal.edit_count != 0
        ):
            return
        if await self.session.get(ScanFeedbackORM, meal.meal_id) is None:
            self.session.add(
                ScanFeedbackORM(
                    meal_id=meal.meal_id,
                    user_id=meal.user_id,
                    original=original_scan_snapshot(meal),
                    consent=False,
                    status="not_shared",
                )
            )
            await self.session.flush()

    async def submit(self, meal_id: str, corrected: dict, kind: str) -> str:
        row = await self.session.get(ScanFeedbackORM, meal_id)
        if row is None:
            raise ValueError(
                "This scan has no original recognition snapshot. Please submit feedback on a new scan."
            )
        if row.original.get("image_id") != corrected.get("image_id"):
            raise ValueError(
                "The scan photo has changed. Feedback must refer to the original image."
            )
        digest = snapshot_hash(
            {"corrected": corrected, "kind": kind, "consent_version": CONSENT_VERSION}
        )
        if row.consent and row.submission_hash == digest:
            return row.status
        row.corrected = corrected
        row.correction_kind = kind
        row.consent = True
        row.consent_version = CONSENT_VERSION
        row.submission_hash = digest
        row.status = "pending"
        row.submitted_at = utc_now()
        row.reviewed_at = None
        row.reviewer = None
        row.review_note = None
        await self.session.flush()
        return "pending"

    async def withdraw(self, meal_id: str) -> None:
        row = await self.session.get(ScanFeedbackORM, meal_id)
        if row is not None:
            row.consent = False
            row.corrected = None
            row.status = "withdrawn"
            row.submission_hash = None
            row.reviewed_at = None
            row.reviewer = None
            row.review_note = None
            await self.session.flush()
