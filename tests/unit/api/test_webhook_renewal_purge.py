"""The renewal trial-push purge runs inside a SAVEPOINT."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.v1.webhook_subscription_lifecycle import handle_renewal


class _RecordingSession:
    """Records SAVEPOINT boundaries and statements in call order."""

    def __init__(self, *, fail_purge: bool = False) -> None:
        self.calls: list[str] = []
        self._fail_purge = fail_purge

    def begin_nested(self):
        session = self

        class _Savepoint:
            async def __aenter__(self):
                session.calls.append("savepoint")

            async def __aexit__(self, exc_type, exc, tb):
                session.calls.append(
                    "rollback_to_savepoint" if exc_type else "release_savepoint"
                )
                return False

        return _Savepoint()

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append(f"execute:{sql.split(' WHERE ')[0]}")
        if self._fail_purge:
            raise RuntimeError('relation "notifications" does not exist')


async def _renew(session: _RecordingSession) -> MagicMock:
    subscription = MagicMock()
    with patch(
        "src.api.routes.v1.webhook_subscription_lifecycle.get_subscription_by_revenuecat_id",
        new_callable=AsyncMock,
        return_value=subscription,
    ):
        await handle_renewal(
            SimpleNamespace(session=session),
            SimpleNamespace(id="user_123"),
            {"app_user_id": "user_123", "expiration_at_ms": 1699478400000},
            event_publisher=MagicMock(publish=AsyncMock()),
        )
    return subscription


@pytest.mark.asyncio
async def test_purge_runs_inside_savepoint():
    session = _RecordingSession()

    subscription = await _renew(session)

    assert subscription.status == "active"
    assert session.calls == [
        "savepoint",
        "execute:DELETE FROM notifications",
        "release_savepoint",
    ]


@pytest.mark.asyncio
async def test_failed_purge_rolls_back_to_savepoint_and_is_swallowed():
    session = _RecordingSession(fail_purge=True)

    subscription = await _renew(session)

    assert subscription.status == "active"
    assert session.calls == [
        "savepoint",
        "execute:DELETE FROM notifications",
        "rollback_to_savepoint",
    ]
