"""Planner telemetry preserves outcomes, correlation isolation and privacy."""

import asyncio

import pytest

from src import planner_observability as metrics
from src.api.middleware import request_logger


@pytest.fixture
def recorded(monkeypatch):
    distributions, logs = [], []
    monkeypatch.setattr(
        metrics, "distribution_metric", lambda *a, **kw: distributions.append((a, kw))
    )
    monkeypatch.setattr(metrics, "log_event", lambda *a, **kw: logs.append((a, kw)))
    return distributions, logs


def test_phase_duration_has_only_bounded_metric_labels(recorded, monkeypatch):
    times = iter([1.0, 1.25])
    monkeypatch.setattr(metrics, "perf_counter", lambda: next(times))
    with metrics.planner_request_context("current", "request-1"):
        with metrics.planner_phase("plan_sql"):
            pass
    distributions, logs = recorded
    assert distributions == [
        (
            ("planner.phase.duration", 250),
            {
                "unit": "millisecond",
                "attributes": {
                    "operation": "current",
                    "phase": "plan_sql",
                    "result": "success",
                },
            },
        )
    ]
    assert logs[0][1]["attributes"]["request_id"] == "request-1"
    assert "request_id" not in distributions[0][1]["attributes"]
    metrics.record_planner_phase("plan_sql", 1)
    assert len(distributions) == 1


@pytest.mark.parametrize(
    "error,result",
    [(ValueError("private prompt"), "error"), (asyncio.CancelledError(), "cancelled")],
)
def test_phase_preserves_exception_without_recording_its_text(recorded, error, result):
    with metrics.planner_request_context("ai_prompt", "request-2"):
        with pytest.raises(type(error)):
            with metrics.planner_phase("ai_attempt"):
                raise error
    distributions, logs = recorded
    assert distributions[0][1]["attributes"]["result"] == result
    assert "private prompt" not in str(distributions + logs)


def test_connector_failure_does_not_change_business_outcome(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(metrics, "distribution_metric", fail)
    with metrics.planner_request_context("current", "request-3"):
        with metrics.planner_phase("plan_sql"):
            result = "saved plan"
    assert result == "saved plan"


@pytest.mark.asyncio
async def test_concurrent_requests_keep_their_context(recorded):
    async def request(operation, request_id):
        with metrics.planner_request_context(operation, request_id):
            await asyncio.sleep(0)
            metrics.record_planner_phase("plan_sql", 0.1)

    await asyncio.gather(request("current", "one"), request("groceries", "two"))
    _, logs = recorded
    contexts = [kw["attributes"] for _, kw in logs]
    assert {(a["operation"], a["request_id"]) for a in contexts} == {
        ("current", "one"),
        ("groceries", "two"),
    }


def test_dynamic_or_private_labels_are_rejected(recorded):
    with pytest.raises(ValueError):
        with metrics.planner_phase("plan-some-user-id"):
            pass
    with pytest.raises(ValueError):
        with metrics.planner_request_context("private recipe", "id"):
            pass
    assert recorded == ([], [])


@pytest.mark.parametrize(
    "path,method,operation",
    [
        ("/v1/meal-plans/current", "GET", "current"),
        ("/v1/meal-plans/generate", "POST", "generate"),
        ("/v1/meal-plans/private-id/groceries", "GET", "groceries"),
        ("/v1/meal-plans/private-id/slots/private-slot/log", "POST", "slot_log"),
        ("/v1/recipes/private-recipe", "GET", "recipe_detail"),
        ("/v1/recipes/private-recipe/micronutrients/enrich", "POST", "recipe_enrich"),
        ("/v1/unknown", "GET", None),
        ("/v1/recipes/a/unknown", "GET", None),
    ],
)
def test_route_labels_discard_resource_ids(path, method, operation):
    assert metrics.planner_operation(path, method) == operation


@pytest.mark.asyncio
async def test_response_start_is_separate_from_background_completion(
    recorded, monkeypatch
):
    clock = [0.0]
    monkeypatch.setattr(request_logger.time, "perf_counter", lambda: clock[0])
    sent = []

    async def app(scope, receive, send):
        clock[0] = 0.05
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})
        clock[0] = 0.25  # Awaited background preparation finishes after body.

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "path": "/v1/meal-plans/current",
        "method": "GET",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
    }
    await request_logger.RequestLoggerMiddleware(app)(scope, receive, send)
    distributions, _ = recorded
    assert [(a[1], kw["attributes"]["phase"]) for a, kw in distributions] == [
        (50, "response_start"),
        (250, "request_complete"),
    ]
    assert sent[0]["headers"]


@pytest.mark.asyncio
async def test_ai_deadline_cancels_auth_before_headers_with_existing_error_envelope(
    recorded, monkeypatch
):
    monkeypatch.setattr(
        request_logger.RequestLoggerMiddleware, "AI_REQUEST_TIMEOUT_SECONDS", 0.01
    )
    cancelled = asyncio.Event()
    sent = []

    async def app(scope, receive, send):
        try:
            await asyncio.Event().wait()  # Represents a blocked dependency.
        finally:
            cancelled.set()

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "path": "/v1/meal-plans/private-id/ai-prompt",
        "method": "POST",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
    }
    await request_logger.RequestLoggerMiddleware(app)(scope, receive, send)
    assert cancelled.is_set()
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 503
    assert b"AI_MEAL_PLAN_UNAVAILABLE" in sent[-1]["body"]
    distributions, _ = recorded
    assert distributions[-1][1]["attributes"]["result"] == "error"


@pytest.mark.asyncio
async def test_non_planner_timeout_keeps_existing_exception_behavior(recorded):
    async def app(scope, receive, send):
        raise TimeoutError("unrelated service timeout")

    async def receive():
        return {"type": "http.request", "body": b""}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "path": "/v1/unrelated",
        "method": "GET",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
    }
    with pytest.raises(TimeoutError, match="unrelated service timeout"):
        await request_logger.RequestLoggerMiddleware(app)(scope, receive, send)
    assert sent == []
    assert recorded == ([], [])
