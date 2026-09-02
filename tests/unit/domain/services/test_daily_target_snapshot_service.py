"""Write-once snapshot hook tests."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.services.daily_target_snapshot_service import maybe_write_today_snapshot


@pytest.mark.asyncio
async def test_writes_when_target_date_is_today():
    repo = MagicMock()
    repo.insert_if_absent = AsyncMock()
    uow = MagicMock(daily_target_snapshots=repo)
    today = date(2026, 9, 1)
    with patch(
        "src.domain.services.daily_target_snapshot_service.datetime"
    ) as mock_dt:
        mock_dt.now.return_value.date.return_value = today
        await maybe_write_today_snapshot(uow, "user-1", today, 1850.0, "UTC")
    repo.insert_if_absent.assert_awaited_once_with("user-1", today, 1850.0)


@pytest.mark.asyncio
async def test_skips_past_dates_no_backfill():
    repo = MagicMock()
    repo.insert_if_absent = AsyncMock()
    uow = MagicMock(daily_target_snapshots=repo)
    with patch(
        "src.domain.services.daily_target_snapshot_service.datetime"
    ) as mock_dt:
        mock_dt.now.return_value.date.return_value = date(2026, 9, 1)
        await maybe_write_today_snapshot(
            uow, "user-1", date(2026, 8, 31), 1850.0, "UTC"
        )
    repo.insert_if_absent.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_repo_missing_on_mock_uow():
    uow = MagicMock(spec=[])
    with patch(
        "src.domain.services.daily_target_snapshot_service.datetime"
    ) as mock_dt:
        mock_dt.now.return_value.date.return_value = date(2026, 9, 1)
        await maybe_write_today_snapshot(
            uow, "user-1", date(2026, 9, 1), 1850.0, "UTC"
        )
