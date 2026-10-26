"""Capability discovery for background AI jobs."""

from unittest.mock import create_autospec

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from tests.fixtures.fakes.fake_ai_jobs import FakeAiJobsUow, StubSchemaProbe

from src.api.dependencies.ai_jobs import get_ai_job_service
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1.capabilities import ai_job_capabilities
from src.api.routes.v1.capabilities import router as capabilities_router
from src.app.services.ai_jobs import AiJobCancelSignals, AiJobService, AiJobWakeup
from src.domain.exceptions.ai_job_exceptions import AiJobsUnavailableError

UNAVAILABLE = {
    "error_code": "AI_JOBS_UNAVAILABLE",
    "message": "Background AI jobs are not available",
}


def _service(
    *, enabled: bool = True, chat_background: bool = True, ready: bool = True
) -> AiJobService:
    return AiJobService(
        uow_factory=FakeAiJobsUow(),
        schema_probe=StubSchemaProbe(ready=ready),
        wakeup=AiJobWakeup(),
        cancel_signals=AiJobCancelSignals(),
        enabled=enabled,
        chat_background_enabled=chat_background,
        lease_seconds=120,
    )


@pytest.mark.asyncio
async def test_ai_job_capabilities_advertise_the_job_contract():
    body = await ai_job_capabilities(service=_service())

    assert body == {
        "ai_jobs": True,
        "types": ["chat", "meal_scan", "parse_text"],
        "max_attempts": 4,
        "poll_interval_ms": 2500,
        "chat_background": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "chat_background", "ai_jobs", "chat"),
    [
        (True, False, True, False),
        (False, True, False, False),
        (False, False, False, False),
    ],
)
async def test_switches_turn_off_jobs_and_background_chat(
    enabled, chat_background, ai_jobs, chat
):
    body = await ai_job_capabilities(
        service=_service(enabled=enabled, chat_background=chat_background)
    )

    assert body["ai_jobs"] is ai_jobs
    assert body["chat_background"] is chat


@pytest.mark.asyncio
async def test_ai_job_capabilities_unavailable_when_schema_missing():
    with pytest.raises(HTTPException) as caught:
        await ai_job_capabilities(service=_service(ready=False))

    assert caught.value.status_code == 503
    assert caught.value.detail == UNAVAILABLE
    assert isinstance(caught.value.__cause__, AiJobsUnavailableError)
    assert caught.value.__cause__.reason == "schema_missing"


@pytest.mark.asyncio
async def test_ai_job_capabilities_unavailable_when_the_check_fails():
    service = create_autospec(AiJobService, instance=True)
    error = RuntimeError("database is down")
    service.capabilities.side_effect = error

    with pytest.raises(HTTPException) as caught:
        await ai_job_capabilities(service=service)

    assert caught.value.status_code == 503
    assert caught.value.detail == UNAVAILABLE
    assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    ("ready", "status_code"), [(True, 200), (False, 503)], ids=["ready", "missing"]
)
def test_ai_job_capabilities_route(ready, status_code):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(capabilities_router)
    app.dependency_overrides[get_ai_job_service] = lambda: _service(ready=ready)

    response = TestClient(app).get("/v1/capabilities/ai-jobs")

    assert response.status_code == status_code
    if ready:
        assert response.json()["types"] == ["chat", "meal_scan", "parse_text"]
    else:
        assert response.json() == {"detail": UNAVAILABLE}
