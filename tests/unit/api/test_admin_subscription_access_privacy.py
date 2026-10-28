"""The support lookup's query, usually a customer's email, reaches neither
the logs nor Sentry.

Sentry is initialised the way the app does it
(SentryObservabilityConnector.initialize: its integrations, before_send and
before_send_transaction, tracing and logs on), with every envelope kept in
memory instead of sent. The routes run on the real event bus and handlers;
only the repository and the RevenueCat refresher are faked.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from tests.unit.api.test_admin_subscription_access import (
    ADMIN,
    LOOKUP,
    PEEK,
    USER,
    _headers,
    _Refresher,
    _Repository,
    _row,
)

from src.api.dependencies.auth import require_admin
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.api.routes.v1 import admin_subscription_access as route_mod
from src.app.commands.subscription_access import ResyncSubscriptionAccessCommand
from src.app.handlers.command_handlers.resync_subscription_access_command_handler import (
    ResyncSubscriptionAccessCommandHandler,
)
from src.app.handlers.query_handlers.get_support_subscription_access_query_handler import (
    GetSupportSubscriptionAccessQueryHandler,
)
from src.app.queries.subscription_access import GetSupportSubscriptionAccessQuery
from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus
from src.infra.monitoring import sentry as sentry_module
from src.observability import (
    reset_observability_connector_for_test,
    set_observability_connector_for_test,
)

FAILING_QUERY = "customer@example.com"  # the database fails on this lookup
MISSING_QUERY = "nobody@example.com"
FOUND_QUERY = USER.email
QUERIES = (FAILING_QUERY, MISSING_QUERY, FOUND_QUERY)


class _CaptureTransport(sentry_sdk.transport.Transport):
    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.envelopes: list = []

    def capture_envelope(self, envelope) -> None:
        self.envelopes.append(envelope)


class _FailingRepository(_Repository):
    async def find_users(self, query, *, limit):
        if query == FAILING_QUERY:
            raise RuntimeError("database is down")
        return await super().find_users(query, limit=limit)


@pytest.fixture
def app_sentry(monkeypatch):
    """The app's own Sentry init, sending into memory."""
    transport = _CaptureTransport()
    real_init = sentry_sdk.init
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    settings = sentry_module.settings
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 1.0)
    monkeypatch.setattr(settings, "SENTRY_PROFILES_SAMPLE_RATE", 0.0)
    monkeypatch.setattr(settings, "SENTRY_PROFILE_SESSION_SAMPLE_RATE", None)
    monkeypatch.setattr(settings, "SENTRY_PROFILE_LIFECYCLE", None)
    monkeypatch.setattr(settings, "SENTRY_SEND_PII", False)
    monkeypatch.setattr(settings, "SENTRY_ENABLE_LOGS", True)
    monkeypatch.setattr(settings, "SENTRY_ENABLE_METRICS", True)
    monkeypatch.setattr(
        sentry_sdk, "init", lambda **options: real_init(transport=transport, **options)
    )
    connector = sentry_module.SentryObservabilityConnector()
    connector.initialize()
    assert connector._enabled
    set_observability_connector_for_test(connector)
    try:
        yield transport
    finally:
        sentry_sdk.flush(timeout=2)
        sentry_sdk.get_client().close(timeout=2)
        real_init(dsn="")
        reset_observability_connector_for_test()


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(route_mod, "forget_revenuecat_check", AsyncMock())
    limiter.clear_memory()
    yield
    limiter.clear_memory()


def _app() -> FastAPI:
    """Built after Sentry's init, as in src/api/main.py, so its routes are
    instrumented."""
    repository = _FailingRepository(rows={USER.id: [_row()]})
    refresher = _Refresher(repository, peek=PEEK)
    bus = PyMediatorEventBus()
    bus.register_handler(
        GetSupportSubscriptionAccessQuery,
        GetSupportSubscriptionAccessQueryHandler(repository, refresher),
    )
    bus.register_handler(
        ResyncSubscriptionAccessCommand,
        ResyncSubscriptionAccessCommandHandler(repository, refresher),
    )
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(route_mod.router)
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    app.dependency_overrides[require_admin] = lambda: ADMIN
    return app


def _items(transport: _CaptureTransport) -> list[tuple[str, dict]]:
    return [
        (item.type, item.payload.json or {})
        for envelope in transport.envelopes
        for item in envelope.items
    ]


def test_lookup_query_reaches_neither_logs_nor_sentry(app_sentry, caplog):
    client = TestClient(_app(), raise_server_exceptions=False)

    with caplog.at_level(logging.DEBUG):
        failed = client.post(LOOKUP, json={"query": FAILING_QUERY}, headers=_headers())
        missed = client.post(LOOKUP, json={"query": MISSING_QUERY}, headers=_headers())
        found = client.post(
            LOOKUP, json={"query": FOUND_QUERY, "live": True}, headers=_headers()
        )
        sentry_sdk.flush(timeout=2)

    assert (failed.status_code, missed.status_code, found.status_code) == (
        500,
        404,
        200,
    )
    # The failure was reported, and every request traced, with the request
    # attached: the scrub had something to scrub.
    items = _items(app_sentry)
    errors = [
        payload
        for kind, payload in items
        if kind == "event" and payload.get("level") == "error"
    ]
    transactions = [payload for kind, payload in items if kind == "transaction"]
    assert errors and len(transactions) == 3
    for payload in errors + transactions:
        assert payload["request"]["url"].endswith(LOOKUP)
        assert payload["request"]["data"] == "[Filtered]"
    sent = b"".join(envelope.serialize() for envelope in app_sentry.envelopes)
    for query in QUERIES:
        assert query.encode() not in sent
    # Nor any log record, from any logger (the access log included).
    for record in caplog.records:
        logged = f"{record.getMessage()} {record.args!r} {vars(record)!r}"
        for query in QUERIES:
            assert query not in logged, record.name


def test_lookup_takes_no_url_parameters():
    """Nothing about the customer goes in the URL the access log records."""
    operation = _app().openapi()["paths"][LOOKUP]["post"]

    assert "parameters" not in operation
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/SupportUserLookupRequest")
