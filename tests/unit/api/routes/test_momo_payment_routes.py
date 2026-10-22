"""MoMo route edges: quiz rate-limit keys, the 503 body and production mode."""

import hashlib
import logging

from starlette.requests import Request

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


def _settings(monkeypatch, environment: str, momo_env: str) -> None:
    for name, value in {
        "ENVIRONMENT": environment,
        "MOMO_ENV": momo_env,
        "MOMO_PARTNER_CODE": "NUTREE",
        "MOMO_ACCESS_KEY": "access",
        "MOMO_SECRET_KEY": "secret",
    }.items():
        monkeypatch.setattr(momo_bootstrap.settings, name, value)


def test_production_api_with_sandbox_momo_is_not_configured(monkeypatch, caplog):
    _settings(monkeypatch, "production", "sandbox")

    with caplog.at_level(logging.ERROR):
        credentials = momo_bootstrap._momo_credentials()

    assert credentials.missing == ["MOMO_ENV"]
    assert "MOMO_ENV=production" in caplog.text


def test_production_api_with_production_momo_is_configured(monkeypatch):
    _settings(monkeypatch, "production", "production")

    assert momo_bootstrap._momo_credentials().missing == []


def test_staging_may_run_the_momo_sandbox(monkeypatch):
    _settings(monkeypatch, "staging", "sandbox")

    assert momo_bootstrap._momo_credentials().missing == []
