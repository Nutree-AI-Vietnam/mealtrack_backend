"""MoMo route edges: rate-limit keys, the 503 body, production mode, emails."""

import hashlib
import logging

import pytest
from starlette.requests import Request

from src.api.routes.v1 import momo_payments as momo_routes
from src.api.routes.v1.momo_payments import checkout_error
from src.api.routes.v1.web_funnel_momo import lead_access_rate_limit_key
from src.bootstrap import momo_payments as momo_bootstrap
from src.bootstrap.momo_payments import MomoNotConfigured

ACCESS_KEY = "k" * 40


def _bff_request(**headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/web-funnel/momo/plans",
            "headers": [
                (name.lower().encode(), value.encode())
                for name, value in headers.items()
            ],
            "client": ("76.76.21.21", 443),
        }
    )


def test_quiz_rate_limit_is_keyed_on_the_lead_access_key_hash():
    request = _bff_request(**{"X-Lead-Access-Key": ACCESS_KEY})

    key = lead_access_rate_limit_key(request)

    assert key == hashlib.sha256(ACCESS_KEY.encode()).hexdigest()
    assert ACCESS_KEY not in key


def test_two_buyers_behind_one_bff_ip_get_separate_buckets():
    first = _bff_request(**{"X-Lead-Access-Key": ACCESS_KEY})
    second = _bff_request(**{"X-Lead-Access-Key": "z" * 40})

    assert lead_access_rate_limit_key(first) != lead_access_rate_limit_key(second)


def test_quiz_rate_limit_falls_back_to_the_default_key_without_an_access_key():
    assert lead_access_rate_limit_key(_bff_request()) == "76.76.21.21"


def test_unconfigured_checkout_hides_setting_names(caplog):
    with caplog.at_level(logging.ERROR):
        error = checkout_error(MomoNotConfigured(["MOMO_SECRET_KEY", "MOMO_ENV"]))

    assert error.status_code == 503
    assert error.detail == "MoMo checkout is unavailable."
    assert "MOMO_SECRET_KEY" in caplog.text


def _settings(
    monkeypatch, environment: str | None, momo_env: str, keys: bool = True
) -> None:
    if environment is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", environment)
    for name, value in {
        "MOMO_ENV": momo_env,
        "MOMO_PARTNER_CODE": "NUTREE" if keys else "",
        "MOMO_ACCESS_KEY": "access" if keys else "",
        "MOMO_SECRET_KEY": "secret" if keys else "",
    }.items():
        monkeypatch.setattr(momo_bootstrap.settings, name, value)


@pytest.mark.parametrize(
    "environment", ["production", "Production", "prod", " ", None], ids=repr
)
def test_api_outside_a_sandbox_stage_refuses_sandbox_momo(
    monkeypatch, caplog, environment
):
    _settings(monkeypatch, environment, "sandbox")

    with caplog.at_level(logging.INFO):
        credentials = momo_bootstrap._momo_credentials()

    assert credentials.missing == ["MOMO_ENV"]
    assert [record.levelno for record in caplog.records] == [logging.ERROR]
    assert "MOMO_ENV=production" in caplog.text


def test_production_api_with_production_momo_is_configured(monkeypatch):
    _settings(monkeypatch, "production", " Production ")

    credentials = momo_bootstrap._momo_credentials()

    assert credentials.missing == []
    assert credentials.base_url == "https://payment.momo.vn"


@pytest.mark.parametrize("environment", ["staging", "development", "local", "test"])
def test_non_production_stages_may_run_the_momo_sandbox(monkeypatch, environment):
    _settings(monkeypatch, environment.upper(), "sandbox")

    assert momo_bootstrap._momo_credentials().missing == []


def test_unconfigured_momo_in_production_is_not_an_error(monkeypatch, caplog):
    _settings(monkeypatch, "production", "sandbox", keys=False)

    with caplog.at_level(logging.INFO):
        credentials = momo_bootstrap._momo_credentials()

    assert "MOMO_SECRET_KEY" in credentials.missing
    assert caplog.records
    assert all(record.levelno == logging.INFO for record in caplog.records)


def test_momo_settings_are_reported_once_per_process(monkeypatch, caplog):
    _settings(monkeypatch, "production", "sandbox", keys=False)
    momo_bootstrap.get_momo_pass_service.cache_clear()
    try:
        with caplog.at_level(logging.INFO, logger=momo_bootstrap.__name__):
            first = momo_bootstrap.get_momo_pass_service()
            second = momo_bootstrap.get_momo_pass_service()
    finally:
        momo_bootstrap.get_momo_pass_service.cache_clear()

    assert first is second
    assert [record.levelno for record in caplog.records] == [logging.INFO]


class _PlansService:
    def __init__(self):
        self.buyers = []

    async def plans_for(self, buyer):
        self.buyers.append(buyer)
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "priced_email"),
    [
        (
            {
                "email": "a@x.vn",
                "email_verified": True,
                "firebase": {"sign_in_provider": "google.com"},
            },
            "a@x.vn",
        ),
        (
            {
                "email": "a@x.vn",
                "email_verified": False,
                "firebase": {"sign_in_provider": "password"},
            },
            None,
        ),
        (
            {
                "email": "a@x.vn",
                "email_verified": True,
                "firebase": {"sign_in_provider": "custom"},
            },
            None,
        ),
    ],
    ids=["verified", "unverified", "custom-token"],
)
async def test_app_prices_use_only_a_proven_email(monkeypatch, token, priced_email):
    service = _PlansService()
    monkeypatch.setattr(momo_routes, "get_momo_pass_service", lambda: service)

    await momo_routes.list_momo_plans(user_id="user-1", token=token)

    assert (service.buyers[0].user_id, service.buyers[0].email) == (
        "user-1",
        priced_email,
    )


class _IpnService:
    def __init__(self, trusted: bool):
        self.trusted = trusted
        self.calls: list[tuple[str, bool]] = []
        self.delivered: list[str] = []

    async def handle_ipn(self, payload, *, deliver=True):
        self.calls.append((payload["orderId"], deliver))
        return self.trusted

    async def deliver(self, order_id):
        self.delivered.append(order_id)


def _ipn_client(monkeypatch, service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(momo_routes, "get_momo_pass_service", lambda: service)
    app = FastAPI()
    app.include_router(momo_routes.webhook_router)
    return TestClient(app)


def test_ipn_answers_before_granting_the_pass(monkeypatch):
    service = _IpnService(trusted=True)

    response = _ipn_client(monkeypatch, service).post(
        "/v1/webhooks/momo/ipn", json={"orderId": "o1", "resultCode": 0}
    )

    assert response.status_code == 204
    assert service.calls == [("o1", False)]
    assert service.delivered == ["o1"]


def test_untrusted_ipn_is_rejected_and_grants_nothing(monkeypatch):
    service = _IpnService(trusted=False)

    response = _ipn_client(monkeypatch, service).post(
        "/v1/webhooks/momo/ipn", json={"orderId": "o1", "resultCode": 0}
    )

    assert response.status_code == 400
    assert service.delivered == []
