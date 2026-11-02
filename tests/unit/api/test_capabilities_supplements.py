"""Capability discovery for supplement logging."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.v1.capabilities import supplement_capabilities
from src.bootstrap import supplements as bootstrap


@pytest.mark.asyncio
async def test_supplements_are_advertised_once_available():
    with patch(
        "src.api.routes.v1.capabilities.supplements_available",
        new=AsyncMock(return_value=True),
    ):
        assert await supplement_capabilities() == {"supplements": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe", [AsyncMock(return_value=False), AsyncMock(side_effect=OSError("db down"))]
)
async def test_supplements_answer_503_while_off_unmigrated_or_unreachable(probe):
    with patch("src.api.routes.v1.capabilities.supplements_available", new=probe):
        with pytest.raises(HTTPException) as raised:
            await supplement_capabilities()

    assert raised.value.status_code == 503
    assert raised.value.detail["error_code"] == "SUPPLEMENTS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_the_switch_off_skips_the_schema_check():
    uow = MagicMock(side_effect=AssertionError("no database call while off"))
    with (
        patch.object(
            bootstrap,
            "get_settings",
            return_value=SimpleNamespace(SUPPLEMENTS_ENABLED=False),
        ),
        patch.object(bootstrap, "AsyncUnitOfWork", uow),
    ):
        assert await bootstrap.supplements_available() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_the_switch_on_reports_whether_the_tables_exist(ready):
    unit = MagicMock()
    unit.supplements.schema_ready = AsyncMock(return_value=ready)
    unit.__aenter__ = AsyncMock(return_value=unit)
    unit.__aexit__ = AsyncMock(return_value=None)
    with (
        patch.object(
            bootstrap,
            "get_settings",
            return_value=SimpleNamespace(SUPPLEMENTS_ENABLED=True),
        ),
        patch.object(bootstrap, "AsyncUnitOfWork", MagicMock(return_value=unit)),
    ):
        assert await bootstrap.supplements_available() is ready
