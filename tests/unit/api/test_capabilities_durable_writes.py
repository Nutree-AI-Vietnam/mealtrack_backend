"""Capability discovery for durable writes."""

from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.v1.capabilities import durable_write_capabilities


@pytest.mark.asyncio
async def test_durable_write_capabilities_keep_meal_edit_on_legacy_contract():
    with patch(
        "src.api.routes.v1.capabilities.durable_write_schema_is_ready",
        new=AsyncMock(return_value=True),
    ):
        body = await durable_write_capabilities()

    assert body["retention_days"] == 14
    assert body["actions"]["manual_meal_create"]["supported"] is True
    assert body["actions"]["manual_meal_create"]["header"] == "Idempotency-Key"
    assert body["actions"]["barcode_meal_create"]["supported"] is True
    assert body["actions"]["barcode_meal_create"]["header"] == "Idempotency-Key"
    assert body["actions"]["barcode_meal_create"]["exact_replay"] is True
    assert body["actions"]["weight_sync"]["supported"] is False
    assert body["actions"]["weight_sync"]["reason"] == "client_entry_id_mapping_pending"
    assert body["durable_writes"] is True
    assert body["nutrition_contract_version"] == 2
    assert body["operations"] == ["create_manual_meal"]
    assert body["actions"]["meal_edit"]["supported"] is False
    assert body["actions"]["meal_edit"]["reason"] == "legacy_v1_edit_path"
