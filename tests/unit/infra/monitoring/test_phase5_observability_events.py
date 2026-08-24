"""TDD guards: Phase 5 structured observability events.

Verifies that each target boundary emits the correct metric/event via the
facade, with safe low-cardinality attributes and no user/meal/resource IDs.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.observability import (
    reset_observability_connector_for_test,
    set_observability_connector_for_test,
)


class _Rec:
    """Minimal recording connector for facade assertions."""

    def __init__(self):
        self.calls: list[tuple] = []

    def initialize(self):
        pass

    def capture_exception(self, error, *, context=None):
        pass

    def capture_message(self, message, *, level="info", context=None):
        pass

    def log_event(self, level, message, *, attributes=None):
        self.calls.append(("log_event", level, message, attributes))

    def increment_metric(self, name, value=1.0, *, unit=None, attributes=None):
        self.calls.append(("increment_metric", name, value, unit, attributes))

    def gauge_metric(self, name, value, *, unit=None, attributes=None):
        pass

    def distribution_metric(self, name, value, *, unit=None, attributes=None):
        self.calls.append(("distribution_metric", name, value, unit, attributes))

    def set_request_context(self, *, request_id, method, path, user_id=None):
        pass

    def start_span(self, *, operation, description=None, context=None):
        return nullcontext()

    def flush(self, *, timeout=5):
        pass


@pytest.fixture(autouse=True)
def _reset_obs():
    yield
    reset_observability_connector_for_test()


# ---------------------------------------------------------------------------
# Manual meal save — db and cache latency distributions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_meal_save_emits_db_and_cache_latency_metrics():
    from src.app.handlers.command_handlers.create_manual_meal_command_handler import (
        CreateManualMealCommandHandler,
    )

    rec = _Rec()
    set_observability_connector_for_test(rec)

    mock_uow = AsyncMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)

    handler = CreateManualMealCommandHandler(uow=mock_uow)
    # Patch _process_meal so we don't need a real DB / domain object
    handler._process_meal = AsyncMock(return_value=(MagicMock(), "2026-06-13"))

    # Minimal command — exact fields don't matter since _process_meal is mocked
    command = MagicMock()
    command.user_id = "user-1"

    await handler.handle(command)

    dist_names = [c[1] for c in rec.calls if c[0] == "distribution_metric"]
    assert "meal.manual_save.db_ms" in dist_names, (
        f"missing db_ms metric, got {dist_names}"
    )
    assert "meal.manual_save.cache_ms" in dist_names, (
        f"missing cache_ms metric, got {dist_names}"
    )

    # No high-cardinality IDs in attributes
    for call in rec.calls:
        if call[0] == "distribution_metric":
            attrs = call[4] or {}
            assert "user_id" not in attrs, (
                "user_id must not appear in metric attributes"
            )
            assert "meal_id" not in attrs


# ---------------------------------------------------------------------------
# AI provider total failure — log_event("warning", "ai.provider.failure")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_all_models_fail_emits_provider_failure_log_event():
    from src.domain.exceptions.ai_exceptions import AIUnavailableError
    from src.domain.model.ai.model_purpose import ModelPurpose
    from src.domain.ports.ai_provider_port import AICapability
    from src.infra.services.ai.ai_model_manager import AIModelManager

    rec = _Rec()
    set_observability_connector_for_test(rec)

    settings = SimpleNamespace(
        OPENAI_API_KEY=None,
        CLOUDFLARE_WORKERS_AI_ENABLED=False,
        CLOUDFLARE_ACCOUNT_ID="",
        CLOUDFLARE_API_TOKEN="",
        CLOUDFLARE_WORKERS_AI_TEXT_MODEL="",
    )
    manager = AIModelManager(settings=settings)
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.supported_capabilities = {AICapability.TEXT_GENERATION}
    provider.generate = AsyncMock(side_effect=RuntimeError("provider down"))
    provider.extract_error_code = MagicMock(return_value=503)
    manager._providers = {"openai": provider}
    manager._model_provider_overrides = {"model-a": "openai"}
    manager._fallback_chains[ModelPurpose.GENERAL] = ["model-a"]
    manager._circuit_breaker = MagicMock()
    manager._circuit_breaker.filter_available = MagicMock(return_value=["model-a"])
    manager._circuit_breaker.should_trip = MagicMock(return_value=False)
    manager._circuit_breaker.record_failure = MagicMock()
    manager._circuit_breaker.record_success = MagicMock()

    with pytest.raises(AIUnavailableError):
        await manager.generate(
            purpose=ModelPurpose.GENERAL,
            prompt="test prompt",
            system_message="sys",
        )

    failure_events = [
        c for c in rec.calls if c[0] == "log_event" and c[2] == "ai.provider.failure"
    ]
    assert len(failure_events) >= 1, "expected ai.provider.failure log_event"

    # Check level and safe attributes
    for ev in failure_events:
        assert ev[1] == "warning", f"expected warning level, got {ev[1]}"
        attrs = ev[3] or {}
        assert "user_id" not in attrs
        assert "model" not in attrs  # model name per-request is high-cardinality
