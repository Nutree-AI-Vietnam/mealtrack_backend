import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from src.api.exceptions import ExternalServiceException
from src.app.handlers.query_handlers.lookup_barcode_query_handler import (
    LookupBarcodeQueryHandler,
)
from src.app.queries.food.lookup_barcode_query import LookupBarcodeQuery


class _DummyRepo:
    def __init__(self):
        self.upserts = []
        self._store = {}

    async def get_by_barcode(self, barcode: str):
        return self._store.get(barcode)

    async def upsert(self, data):
        self.upserts.append(data)
        saved = dict(data)
        saved.setdefault("id", 123)
        self._store[data.get("barcode")] = saved
        return saved


class _DummyUow:
    def __init__(self, repo):
        self.food_references = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class _DummyUowFactory:
    def __init__(self, repo):
        self.repo = repo

    def __call__(self):
        return _DummyUow(self.repo)


@pytest.mark.asyncio
async def test_fast_fatsecret_does_not_launch_off():
    repo = _DummyRepo()
    fs_mock = AsyncMock()
    fs_mock.get_product.return_value = {
        "name": "Quick Oats",
        "protein_100g": 13.0,
        "carbs_100g": 68.0,
        "fat_100g": 7.0,
        "calories_100g": 389.0,
        "source": "fatsecret",
    }
    off_mock = AsyncMock()

    handler = LookupBarcodeQueryHandler(
        open_food_facts_service=off_mock,
        fat_secret_service=fs_mock,
        async_uow_factory=_DummyUowFactory(repo),
        hedge_delay_seconds=0.1,
    )

    query = LookupBarcodeQuery(
        barcode="00012345678905",
        scanned_barcode="00012345678905",
        language="en",
    )

    result = await handler.handle(query)
    assert result is not None
    assert result["name"] == "Quick Oats"
    assert result["source"] == "fatsecret"
    off_mock.get_product.assert_not_called()


@pytest.mark.asyncio
async def test_slow_fatsecret_hedges_with_off():
    repo = _DummyRepo()
    fs_mock = AsyncMock()

    async def slow_fs(*_args, **_kwargs):
        await asyncio.sleep(0.5)
        return {
            "name": "Slow FS Oats",
            "protein_100g": 13.0,
            "carbs_100g": 68.0,
            "fat_100g": 7.0,
            "calories_100g": 389.0,
        }

    fs_mock.get_product.side_effect = slow_fs

    off_mock = AsyncMock()
    off_mock.get_product.return_value = {
        "name": "Fast OFF Oats",
        "protein_100g": 12.0,
        "carbs_100g": 67.0,
        "fat_100g": 6.5,
        "calories_100g": 380.0,
        "source": "openfoodfacts",
    }

    handler = LookupBarcodeQueryHandler(
        open_food_facts_service=off_mock,
        fat_secret_service=fs_mock,
        async_uow_factory=_DummyUowFactory(repo),
        hedge_delay_seconds=0.05,
    )

    query = LookupBarcodeQuery(
        barcode="00012345678905",
        scanned_barcode="00012345678905",
        language="en",
    )

    result = await handler.handle(query)
    assert result is not None
    assert result["name"] == "Fast OFF Oats"
    assert result["source"] == "openfoodfacts"
    off_mock.get_product.assert_called_once()


@pytest.mark.asyncio
async def test_request_wide_timeout_raises_external_service_exception_and_cleans_up_tasks():
    repo = _DummyRepo()
    fs_mock = AsyncMock()

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(2.0)
        return None

    fs_mock.get_product.side_effect = hang
    off_mock = AsyncMock()
    off_mock.get_product.side_effect = hang

    handler = LookupBarcodeQueryHandler(
        open_food_facts_service=off_mock,
        fat_secret_service=fs_mock,
        async_uow_factory=_DummyUowFactory(repo),
        request_timeout_seconds=0.1,
        hedge_delay_seconds=0.05,
    )

    query = LookupBarcodeQuery(
        barcode="00012345678905",
        scanned_barcode="00012345678905",
        language="en",
    )

    with pytest.raises(ExternalServiceException) as exc_info:
        await handler.handle(query)
    assert exc_info.value.error_code == "BARCODE_LOOKUP_TIMEOUT"


@pytest.mark.asyncio
async def test_repeated_barcode_scan_maintains_estimate_status():
    repo = _DummyRepo()
    handler = LookupBarcodeQueryHandler(
        open_food_facts_service=AsyncMock(get_product=AsyncMock(return_value=None)),
        fat_secret_service=AsyncMock(get_product=AsyncMock(return_value=None)),
        async_uow_factory=_DummyUowFactory(repo),
    )

    # 1. First scan generates an AI estimate
    estimate_payload = {
        "name": "Estimated Homemade Cookies",
        "protein_100g": 5.0,
        "carbs_100g": 60.0,
        "fat_100g": 20.0,
        "calories_100g": 440.0,
        "source": "ai_estimate",
        "is_food": True,
    }
    handler._ai_estimate = AsyncMock(return_value=estimate_payload)

    query = LookupBarcodeQuery(
        barcode="00012345678999",
        scanned_barcode="00012345678999",
        language="en",
    )

    first_res = await handler.handle(query)
    assert first_res is not None
    assert first_res["is_estimate"] is True
    assert first_res["source"] == "ai_estimate"

    # 2. Second scan should NOT trust the cached row as a verified catalog item
    second_res = await handler.handle(query)
    assert second_res is not None
    assert second_res["is_estimate"] is True


@pytest.mark.asyncio
async def test_partial_fatsecret_name_is_passed_to_ai_estimate():
    repo = _DummyRepo()
    fs_mock = AsyncMock()
    fs_mock.get_product.return_value = {"name": "Partial Oats"}
    off_mock = AsyncMock()
    off_mock.get_product.return_value = None

    handler = LookupBarcodeQueryHandler(
        open_food_facts_service=off_mock,
        fat_secret_service=fs_mock,
        async_uow_factory=_DummyUowFactory(repo),
        hedge_delay_seconds=0.01,
    )
    handler._ai_estimate = AsyncMock(
        return_value={
            "name": "Estimated Oats",
            "protein_100g": 13.0,
            "carbs_100g": 68.0,
            "fat_100g": 7.0,
            "calories_100g": 389.0,
            "source": "ai_estimate",
            "is_food": True,
        }
    )

    query = LookupBarcodeQuery(
        barcode="00012345678905",
        scanned_barcode="00012345678905",
        language="en",
    )
    result = await handler.handle(query)

    assert result is not None
    assert result["is_estimate"] is True
    handler._ai_estimate.assert_awaited_once()
    assert handler._ai_estimate.await_args.args[2] == "Partial Oats"


@pytest.mark.asyncio
async def test_trusted_provider_miss_reasons_are_logged(caplog):
    repo = _DummyRepo()
    fs_mock = AsyncMock()
    fs_mock.get_product.return_value = {"name": "Partial Oats"}
    off_mock = AsyncMock()
    off_mock.get_product.return_value = None

    handler = LookupBarcodeQueryHandler(
        open_food_facts_service=off_mock,
        fat_secret_service=fs_mock,
        async_uow_factory=_DummyUowFactory(repo),
        hedge_delay_seconds=0.01,
    )
    handler._ai_estimate = AsyncMock(return_value=None)

    query = LookupBarcodeQuery(
        barcode="00012345678905",
        scanned_barcode="00012345678905",
        language="en",
    )
    with caplog.at_level(logging.WARNING):
        result = await handler.handle(query)

    assert result is None
    assert "fatsecret_partial_no_nutrition" in caplog.text
    assert "openfoodfacts_empty" in caplog.text
    assert "usda_fdc_empty" in caplog.text
