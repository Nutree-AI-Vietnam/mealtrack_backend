"""Registering the devices that receive AI job pushes."""

import pytest
from tests.fixtures.fakes.fake_ai_jobs import USER_ID, FakeAiJobsUow, StubSchemaProbe

from src.app.services.ai_jobs.push_token_service import PushTokenService
from src.domain.model.notification import DeviceType

TOKEN = "fcm-token-1"


def _service(*, ready: bool = True) -> tuple[FakeAiJobsUow, PushTokenService]:
    uow = FakeAiJobsUow()
    service = PushTokenService(uow_factory=uow, schema_probe=StubSchemaProbe(ready))
    return uow, service


@pytest.mark.asyncio
@pytest.mark.parametrize("device_type", list(DeviceType), ids=lambda d: d.value)
async def test_register_stores_the_device_token(device_type):
    uow, service = _service()

    registered = await service.register(
        user_id=USER_ID, token=TOKEN, device_type=device_type
    )

    assert registered is True
    uow.push_tokens.upsert.assert_awaited_once_with(
        user_id=USER_ID, token=TOKEN, device_type=device_type
    )
    assert uow.committed == 1


@pytest.mark.asyncio
async def test_register_reports_false_before_token_storage_is_migrated():
    uow, service = _service(ready=False)

    registered = await service.register(
        user_id=USER_ID, token=TOKEN, device_type=DeviceType.IOS
    )

    assert registered is False
    uow.push_tokens.upsert.assert_not_awaited()
    assert uow.opened == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("was_active", [True, False], ids=["active", "unknown"])
async def test_unregister_deactivates_the_token(was_active):
    uow, service = _service()
    uow.push_tokens.deactivate.return_value = was_active

    assert await service.unregister(user_id=USER_ID, token=TOKEN) is True
    uow.push_tokens.deactivate.assert_awaited_once_with(user_id=USER_ID, token=TOKEN)
    assert uow.committed == 1


@pytest.mark.asyncio
async def test_unregister_reports_false_before_token_storage_is_migrated():
    uow, service = _service(ready=False)

    assert await service.unregister(user_id=USER_ID, token=TOKEN) is False
    uow.push_tokens.deactivate.assert_not_awaited()
    assert uow.opened == 0


@pytest.mark.asyncio
async def test_a_failed_registration_rolls_back_and_propagates():
    uow, service = _service()
    uow.push_tokens.upsert.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        await service.register(user_id=USER_ID, token=TOKEN, device_type=DeviceType.IOS)

    assert uow.rolled_back == 1
    assert uow.committed == 0


@pytest.mark.asyncio
async def test_a_failed_unregistration_rolls_back_and_propagates():
    uow, service = _service()
    uow.push_tokens.deactivate.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        await service.unregister(user_id=USER_ID, token=TOKEN)

    assert uow.rolled_back == 1
    assert uow.committed == 0
