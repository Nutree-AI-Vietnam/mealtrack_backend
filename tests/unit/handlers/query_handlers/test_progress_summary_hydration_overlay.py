from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.handlers.query_handlers.progress_summary_support import (
    overlay_live_hydration,
)


@pytest.mark.asyncio
async def test_overlay_live_hydration_replaces_cached_ml() -> None:
    uow = AsyncMock()
    uow.hydration_entries.sum_ml_by_date_range.return_value = {
        date(2026, 9, 12): 750,
    }
    payload = await overlay_live_hydration(
        uow,
        "user-1",
        date(2026, 9, 12),
        date(2026, 9, 12),
        "Asia/Ho_Chi_Minh",
        {
            "days": [
                {"date": "2026-09-12", "hydration_ml": 0, "calories": 1800},
            ]
        },
    )
    assert payload["days"][0]["hydration_ml"] == 750
    assert payload["days"][0]["calories"] == 1800
