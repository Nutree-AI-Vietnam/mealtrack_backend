"""Hiding the app's allergy and health cards from the server.

The app shows them only when GET /v1/users/metrics sends an "allergies"
key: it reads the key's presence as the server supporting allergies, not a
flag of its own. So hiding the cards means leaving the key out, and the
saved answers stay where they are and keep excluding recipes.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.responses import JSONResponse

from src.api.base_dependencies import user_metrics_hidden_fields
from src.api.routes.v1.user_profiles import get_user_metrics
from src.api.schemas.response.user_responses import UserMetricsResponse

_ROUTE = "src.api.routes.v1.user_profiles.user_metrics_hidden_fields"


def _metrics() -> dict:
    return {
        "user_id": "u1",
        "age": 30,
        "date_of_birth": None,
        "gender": "female",
        "height_cm": 160.0,
        "weight_kg": 55.0,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 45,
        "fitness_goal": "cut",
        "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
        "allergies": ["shellfish"],
        "health_conditions": ["pregnancy"],
    }


async def _call() -> object:
    bus = AsyncMock()
    bus.send.return_value = _metrics()
    return await get_user_metrics(user_id="u1", event_bus=bus)


class TestTheGateway:
    def test_on_by_default_hides_nothing(self):
        with patch("src.infra.config.settings.get_settings") as settings:
            settings.return_value = SimpleNamespace(USER_METRICS_ALLERGIES_ENABLED=True)
            assert user_metrics_hidden_fields() == frozenset()

    def test_off_hides_both_keys(self):
        with patch("src.infra.config.settings.get_settings") as settings:
            settings.return_value = SimpleNamespace(
                USER_METRICS_ALLERGIES_ENABLED=False
            )
            assert user_metrics_hidden_fields() == frozenset(
                {"allergies", "health_conditions"}
            )

    def test_a_settings_object_without_the_flag_keeps_sending(self):
        """An older deployment must not lose its cards to a missing name."""
        with patch("src.infra.config.settings.get_settings") as settings:
            settings.return_value = SimpleNamespace()
            assert user_metrics_hidden_fields() == frozenset()


class TestTheResponse:
    @pytest.mark.asyncio
    async def test_on_sends_the_saved_answers(self):
        with patch(_ROUTE, return_value=frozenset()):
            result = await _call()

        assert isinstance(result, UserMetricsResponse)
        assert result.allergies == ["shellfish"]
        assert result.health_conditions == ["pregnancy"]

    @pytest.mark.asyncio
    async def test_off_leaves_the_keys_out_rather_than_emptying_them(self):
        """An empty list still reads as "supported" to the app."""
        with patch(_ROUTE, return_value=frozenset({"allergies", "health_conditions"})):
            result = await _call()

        assert isinstance(result, JSONResponse)
        body = json.loads(result.body)
        assert "allergies" not in body
        assert "health_conditions" not in body

    @pytest.mark.asyncio
    async def test_off_keeps_every_other_key_the_app_reads(self):
        """date_of_birth is detected the same way; dropping it would turn
        off a second feature nobody asked about."""
        with patch(_ROUTE, return_value=frozenset({"allergies", "health_conditions"})):
            hidden = json.loads((await _call()).body)
        with patch(_ROUTE, return_value=frozenset()):
            shown = (await _call()).model_dump(mode="json")

        assert "date_of_birth" in hidden
        assert hidden["date_of_birth"] is None
        assert set(shown) - set(hidden) == {"allergies", "health_conditions"}
