"""Planner aggregate budgets, retries and admission release are deterministic."""

import asyncio
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.domain.exceptions.ai_exceptions import AIUnavailableError
from src.infra.services.ai.planner_generation_policy import PlannerGenerationPolicy
from src.planner_request_policy import planner_deadline


def _provider(side_effect=None):
    return SimpleNamespace(
        generate=AsyncMock(side_effect=side_effect, return_value={"ok": True}),
        extract_error_code=lambda error: getattr(error, "status_code", None),
    )


def _circuit():
    return Mock(
        filter_available=Mock(return_value=["model"]),
        should_trip=Mock(return_value=True),
    )


async def _generate(policy, provider, circuit=None):
    return await policy.generate(
        provider=provider,
        model="model",
        circuit_breaker=circuit or _circuit(),
        prompt="private request",
        system_message="rules",
        schema=None,
        max_tokens=1800,
    )


@pytest.mark.asyncio
async def test_one_transient_retry_uses_same_remaining_budget():
    error = RuntimeError("rate limited")
    error.status_code = 429
    error.response = SimpleNamespace(headers={"retry-after": "0.001"})
    provider = _provider([error, {"ok": True}])
    with planner_deadline(monotonic() + 0.5):
        assert await _generate(PlannerGenerationPolicy(), provider) == {"ok": True}
    assert provider.generate.await_count == 2
    budgets = [
        c.kwargs["request_timeout_seconds"] for c in provider.generate.await_args_list
    ]
    assert 0 < budgets[1] <= budgets[0] <= 0.5
    assert all(
        c.kwargs["purpose_hint"] == "meal_plan_adjustment"
        for c in provider.generate.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 400, 429, 503])
async def test_permanent_failures_have_one_attempt_and_transient_at_most_two(status):
    error = RuntimeError("private details")
    error.status_code = status
    error.response = SimpleNamespace(headers={"retry-after": "0"})
    provider = _provider(error)
    with pytest.raises(AIUnavailableError) as failure:
        await _generate(PlannerGenerationPolicy(), provider)
    assert provider.generate.await_count == (2 if status in {429, 503} else 1)
    assert "private details" not in str(failure.value)


@pytest.mark.asyncio
async def test_validation_error_does_not_retry_when_payload_contains_status_number():
    error = ValueError("invalid JSON 429")
    error.status_code = 429
    provider = _provider(error)
    with pytest.raises(AIUnavailableError):
        await _generate(PlannerGenerationPolicy(), provider)
    assert provider.generate.await_count == 1


@pytest.mark.asyncio
async def test_retry_after_exceeding_budget_makes_no_second_call():
    error = RuntimeError("rate limited")
    error.status_code = 429
    error.response = SimpleNamespace(headers={"retry-after": "5"})
    provider = _provider(error)
    with planner_deadline(monotonic() + 0.05):
        with pytest.raises(TimeoutError):
            await _generate(PlannerGenerationPolicy(), provider)
    assert provider.generate.await_count == 1


@pytest.mark.asyncio
async def test_deadline_cancels_provider_and_releases_admission(monkeypatch):
    monkeypatch.setenv("MEAL_PLAN_AI_CAPACITY_PER_PROCESS", "1")
    cancelled = asyncio.Event()

    async def slow(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    policy = PlannerGenerationPolicy()
    provider = _provider(slow)
    with planner_deadline(monotonic() + 0.01):
        with pytest.raises(TimeoutError):
            await _generate(policy, provider)
    assert cancelled.is_set()
    assert policy._slots._value == 1
    assert await _generate(policy, _provider()) == {"ok": True}


@pytest.mark.asyncio
async def test_saturated_admission_does_not_create_provider_call(monkeypatch):
    monkeypatch.setenv("MEAL_PLAN_AI_CAPACITY_PER_PROCESS", "1")
    started, finish = asyncio.Event(), asyncio.Event()

    async def holding(**kwargs):
        started.set()
        await finish.wait()
        return {"ok": True}

    policy = PlannerGenerationPolicy()
    provider = _provider(holding)
    first = asyncio.create_task(_generate(policy, provider))
    await started.wait()
    with planner_deadline(monotonic() + 0.01):
        with pytest.raises(TimeoutError):
            await _generate(policy, provider)
    assert provider.generate.await_count == 1
    finish.set()
    await first
    assert policy._slots._value == 1


@pytest.mark.asyncio
async def test_user_cancellation_releases_admission(monkeypatch):
    monkeypatch.setenv("MEAL_PLAN_AI_CAPACITY_PER_PROCESS", "1")
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.Event().wait()

    policy = PlannerGenerationPolicy()
    task = asyncio.create_task(_generate(policy, _provider(slow)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert policy._slots._value == 1


@pytest.mark.asyncio
async def test_open_circuit_does_not_force_attempt():
    provider = _provider()
    circuit = _circuit()
    circuit.filter_available.return_value = []
    with pytest.raises(AIUnavailableError):
        await _generate(PlannerGenerationPolicy(), provider, circuit)
    assert provider.generate.await_count == 0
