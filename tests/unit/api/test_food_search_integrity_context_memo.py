"""The food search cache namespace is read from the DB at most every few seconds."""

import time
from types import SimpleNamespace

import pytest

from src.api.dependencies import event_bus


class _IntegrityControls:
    def __init__(self):
        self.reads = 0
        self.generation = 3

    async def get_active_control(self):
        self.reads += 1
        return SimpleNamespace(
            active_policy_version="policy-v2",
            catalog_integrity_generation=self.generation,
        )


@pytest.fixture
def controls(monkeypatch):
    controls = _IntegrityControls()

    class _Uow:
        food_reference_integrity = controls

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(event_bus, "AsyncUnitOfWork", _Uow)
    monkeypatch.setattr(event_bus, "_integrity_context_memo", None)
    return controls


@pytest.mark.asyncio
async def test_repeat_reads_within_the_ttl_reuse_the_first_answer(controls):
    first = await event_bus._food_integrity_cache_context()
    controls.generation = 4
    second = await event_bus._food_integrity_cache_context()

    assert first == {"policy_version": "policy-v2", "generation": 3}
    assert second == first
    assert controls.reads == 1


@pytest.mark.asyncio
async def test_callers_cannot_change_the_remembered_answer(controls):
    first = await event_bus._food_integrity_cache_context()
    first["generation"] = 99

    second = await event_bus._food_integrity_cache_context()

    assert second["generation"] == 3


@pytest.mark.asyncio
async def test_expired_answer_is_read_again(controls, monkeypatch):
    expired_at = time.monotonic() - event_bus._INTEGRITY_CONTEXT_TTL_SECONDS - 1
    monkeypatch.setattr(
        event_bus,
        "_integrity_context_memo",
        (expired_at, {"policy_version": "policy-v1", "generation": 1}),
    )

    context = await event_bus._food_integrity_cache_context()

    assert context == {"policy_version": "policy-v2", "generation": 3}
    assert controls.reads == 1


@pytest.mark.asyncio
async def test_shutdown_drains_the_shared_background_work(monkeypatch):
    drained = []

    class _Background:
        async def drain(self, timeout):
            drained.append(timeout)

    monkeypatch.setattr(event_bus, "_food_search_background", _Background())

    await event_bus.drain_food_search_background_work(timeout=2.0)

    assert drained == [2.0]
