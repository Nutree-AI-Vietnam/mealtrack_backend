from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded

from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.preview_body_limit import PreviewBodyLimitMiddleware
from src.api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.api.routes.v1 import tdee as tdee_routes
from src.api.schemas.request.tdee_requests import TdeeCalculationRequest
from src.app.handlers.query_handlers.preview_tdee_query_handler import (
    PreviewTdeeQueryHandler,
)
from src.app.queries.tdee.preview_tdee_query import PreviewTdeeQuery
from src.domain.model.user import Goal, JobType, MacroPreset, Sex, TdeeRequest
from src.domain.services.tdee_service import TdeeCalculationService


def preview_payload(**overrides):
    payload = {
        "age": 30,
        "sex": "male",
        "height": 180,
        "weight": 80,
        "job_type": "desk",
        "training_days_per_week": 0,
        "training_minutes_per_session": 0,
        "goal": "recomp",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def preview_client(monkeypatch):
    app = FastAPI()
    app.add_middleware(PreviewBodyLimitMiddleware)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(tdee_routes.router)
    event_bus = type(
        "EventBus",
        (),
        {
            "send": AsyncMock(
                return_value={
                    "bmr": 1700.0,
                    "tdee": 2200.0,
                    "macros": {
                        "calories": 2200.0,
                        "protein": 150.0,
                        "carbs": 250.0,
                        "fat": 66.7,
                    },
                    "activity_multiplier": 1.2,
                    "formula_used": "mifflin_st_jeor",
                    "is_custom": False,
                    "macro_preset": "standard",
                    "calculation_contract": "onboarding_preview_v2",
                }
            )
        },
    )()
    app.dependency_overrides[get_configured_event_bus] = lambda: event_bus
    monkeypatch.setattr(tdee_routes, "get_configured_event_bus", lambda: event_bus)
    limiter._storage.reset()
    yield TestClient(app)
    limiter._storage.reset()


def test_preview_route_accepts_real_request_and_uses_ip_quota(preview_client):
    payload = preview_payload()
    bearer = "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJ1c2VyLTEifQ."

    first = preview_client.post("/v1/tdee/preview", json=payload)
    assert first.status_code == 200
    assert first.json()["calculation_contract"] == "onboarding_preview_v2"

    for _ in range(9):
        assert (
            preview_client.post(
                "/v1/tdee/preview", json=payload, headers={"Authorization": bearer}
            ).status_code
            == 200
        )

    exhausted = preview_client.post("/v1/tdee/preview", json=payload)
    assert exhausted.status_code == 429
    assert exhausted.json() == {
        "error": "rate_limit_exceeded",
        "code": "preview_rate_limited",
    }
    assert exhausted.headers["Retry-After"]


def test_preview_route_returns_stable_413_for_oversized_body(preview_client):
    response = preview_client.post("/v1/tdee/preview", content=b"x" * 8_193)

    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "request_too_large"}}


def test_preview_normalizes_legacy_no_training_pair():
    request = TdeeCalculationRequest(**preview_payload(training_minutes_per_session=15))
    assert (request.training_days_per_week, request.training_minutes_per_session) == (
        0,
        0,
    )


@pytest.mark.parametrize("days,minutes", [(0, 30), (1, 0), (8, 30)])
def test_preview_rejects_noncanonical_training_pairs(days, minutes):
    with pytest.raises(ValidationError):
        TdeeCalculationRequest(
            **preview_payload(
                training_days_per_week=days, training_minutes_per_session=minutes
            )
        )


def test_preview_rejects_partial_or_conflicting_macro_overrides():
    with pytest.raises(ValidationError):
        TdeeCalculationRequest(**preview_payload(custom_protein_g=100))
    with pytest.raises(ValidationError):
        TdeeCalculationRequest(
            **preview_payload(
                custom_protein_g=100,
                custom_carbs_g=150,
                custom_fat_g=60,
                requested_calories=2000,
            )
        )


def test_keto_preset_keeps_weight_based_base_macros():
    """Selecting keto must not rewrite base daily macros."""
    service = TdeeCalculationService()
    request_kwargs = dict(
        age=30,
        sex=Sex.MALE,
        height=180,
        weight=80,
        body_fat_pct=None,
        job_type=JobType.DESK,
        training_days_per_week=0,
        training_minutes_per_session=0,
        goal=Goal.RECOMP,
    )
    standard = service.calculate_tdee(
        TdeeRequest(**request_kwargs, macro_preset=MacroPreset.STANDARD)
    )
    keto = service.calculate_tdee(
        TdeeRequest(**request_kwargs, macro_preset=MacroPreset.KETO)
    )
    assert keto.macro_preset is MacroPreset.KETO
    assert keto.macros == standard.macros
    assert (
        keto.macros.calories
        == keto.macros.protein * 4 + keto.macros.carbs * 4 + keto.macros.fat * 9
    )


def test_adjusted_keto_policy_preserves_redistributed_macros():
    result_macros = (
        TdeeCalculationService()
        .calculate_tdee(
            TdeeRequest(
                age=30,
                sex=Sex.MALE,
                height=180,
                weight=80,
                body_fat_pct=None,
                job_type=JobType.DESK,
                training_days_per_week=0,
                training_minutes_per_session=0,
                goal=Goal.RECOMP,
            )
        )
        .macros
    )
    adjusted = TdeeCalculationService.apply_adjusted_macro_policy(
        1900,
        result_macros,
        MacroPreset.KETO,
        False,
    )
    assert adjusted is result_macros


def test_custom_adjusted_policy_preserves_existing_macro_targets():
    original = (
        TdeeCalculationService()
        .calculate_tdee(
            TdeeRequest(
                age=30,
                sex=Sex.MALE,
                height=180,
                weight=80,
                body_fat_pct=None,
                job_type=JobType.DESK,
                training_days_per_week=0,
                training_minutes_per_session=0,
                goal=Goal.RECOMP,
            )
        )
        .macros
    )
    assert (
        TdeeCalculationService.apply_adjusted_macro_policy(
            1900, original, MacroPreset.KETO, True
        )
        is original
    )


@pytest.mark.asyncio
async def test_preview_custom_macros_override_keto_but_keep_resolved_preset():
    result = await PreviewTdeeQueryHandler().handle(
        PreviewTdeeQuery(
            age=30,
            sex="male",
            height=180,
            weight=80,
            job_type="desk",
            training_days_per_week=0,
            training_minutes_per_session=0,
            goal="recomp",
            diet_type="keto",
            custom_protein_g=100,
            custom_carbs_g=120,
            custom_fat_g=60,
        )
    )
    assert result["macro_preset"] == "keto"
    assert result["calculation_contract"] == "onboarding_preview_v2"
    assert result["is_custom"] is True
    assert result["macros"] == {
        "protein": 100,
        "carbs": 120,
        "fat": 60,
        "calories": 1420,
    }
