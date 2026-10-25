import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "retry_open_wearables_deletions.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "retry_open_wearables_deletions", _SCRIPT_PATH
)
assert _SPEC is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.asyncio
async def test_retry_clears_confirmed_rows_and_counts_the_rest():
    confirmed, failing = str(uuid4()), str(uuid4())
    uow = FakeUnitOfWork()
    uow.users.open_wearables_user_ids = {confirmed: "ow-ok", failing: "ow-down"}

    async def delete_user(ow_user_id):
        if ow_user_id == "ow-down":
            raise RuntimeError("unreachable")
        return True

    deleted, still_linked = await _MODULE.retry_deletions(
        [(confirmed, "ow-ok"), (failing, "ow-down")],
        MagicMock(delete_user=delete_user),
        uow_factory=lambda: uow,
    )

    assert (deleted, still_linked) == (1, 1)
    assert uow.users.open_wearables_user_ids == {failing: "ow-down"}


@pytest.mark.asyncio
async def test_listing_without_execute_never_calls_open_wearables(monkeypatch):
    monkeypatch.setattr(
        _MODULE,
        "find_outstanding_links",
        AsyncMock(return_value=[(str(uuid4()), "ow-1")]),
    )
    service = MagicMock()
    monkeypatch.setattr(_MODULE, "get_wearable_account_service", service)

    assert await _MODULE.run(execute=False, limit=None) == 0
    service.assert_not_called()


@pytest.mark.asyncio
async def test_execute_fails_when_open_wearables_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        _MODULE,
        "find_outstanding_links",
        AsyncMock(return_value=[(str(uuid4()), "ow-1")]),
    )
    monkeypatch.setattr(_MODULE, "get_wearable_account_service", lambda: None)

    assert await _MODULE.run(execute=True, limit=None) == 1


@pytest.mark.asyncio
async def test_execute_exits_nonzero_while_rows_stay_linked(monkeypatch):
    monkeypatch.setattr(
        _MODULE,
        "find_outstanding_links",
        AsyncMock(return_value=[(str(uuid4()), "ow-1")]),
    )
    monkeypatch.setattr(_MODULE, "get_wearable_account_service", MagicMock)
    monkeypatch.setattr(_MODULE, "retry_deletions", AsyncMock(return_value=(0, 1)))

    assert await _MODULE.run(execute=True, limit=None) == 1
