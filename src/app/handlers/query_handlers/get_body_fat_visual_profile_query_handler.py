"""Read visual body-fat profile history independently from measured metrics."""

from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.user.get_body_fat_visual_profile_query import (
    GetBodyFatVisualProfileQuery,
)
from src.domain.model.user.body_fat_visual import BodyFatVisualProfileSelection
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort


@handles(GetBodyFatVisualProfileQuery)
class GetBodyFatVisualProfileQueryHandler(
    EventHandler[GetBodyFatVisualProfileQuery, dict[str, Any] | None]
):
    """Return immutable selection history when a selection exists."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        uow_factory: Any = None,
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)

    async def handle(
        self, query: GetBodyFatVisualProfileQuery
    ) -> dict[str, Any] | None:
        async with self.uow_factory() as uow:
            history = [
                self._serialize(record)
                for record in await uow.body_fat_visual_profiles.find_history_by_user(
                    query.user_id
                )
            ]

        if not history:
            return None

        latest = history[-1]
        return {
            "schema_version": latest["schema_version"],
            "range_catalog_version": latest["range_catalog_version"],
            "sex_at_selection": latest["sex_at_selection"],
            "start_range_id": latest["start_range_id"],
            "current_range_id": latest["current_range_id"],
            "target_range_id": latest["target_range_id"],
            "updated_at": latest["updated_at"],
            "history": history,
        }

    @staticmethod
    def _serialize(record: BodyFatVisualProfileSelection) -> dict[str, Any]:
        return {
            "schema_version": record.schema_version,
            "range_catalog_version": record.range_catalog_version,
            "sex_at_selection": record.sex_at_selection,
            "start_range_id": record.start_range_id,
            "current_range_id": record.current_range_id,
            "target_range_id": record.target_range_id,
            "updated_at": record.updated_at,
        }
