"""Settings and the Firebase service account decide whether the Play adapter runs."""

import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport.requests import Request as GoogleAuthRequest

from src.domain.ports.play_subscription_port import (
    PlayBillingConfigurationError,
    PlayBillingUnavailableError,
)
from src.infra.adapters.google_play_auth import (
    ANDROID_PUBLISHER_SCOPE,
    ServiceAccountAccessToken,
    _BoundedRequest,
)
from src.infra.adapters.google_play_subscription_adapter import (
    GooglePlaySubscriptionAdapter,
)

ADAPTER = "src.infra.adapters.google_play_subscription_adapter"


@pytest.fixture(scope="module")
def service_account_json() -> str:
    """A throwaway service account key, made for this test run only."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "nutree-test",
            "private_key_id": "test-key-id",
            "private_key": pem,
            "client_email": "firebase-adminsdk@nutree-test.iam.gserviceaccount.com",
            "client_id": "1",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


def _settings(**overrides):
    values = {
        "GOOGLE_PLAY_PACKAGE_NAME": "com.nutreeai.mobile",
        "GOOGLE_PLAY_CANCEL_SANDBOX": False,
        "GOOGLE_PLAY_API_TIMEOUT_SECONDS": 3.0,
        "FIREBASE_CREDENTIALS": None,
        "FIREBASE_SERVICE_ACCOUNT_JSON": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --- from_settings ---


def test_a_configured_environment_gets_an_enabled_adapter(service_account_json):
    adapter = GooglePlaySubscriptionAdapter.from_settings(
        _settings(
            FIREBASE_SERVICE_ACCOUNT_JSON=service_account_json,
            GOOGLE_PLAY_PACKAGE_NAME=" com.nutreeai.mobile.staging ",
            GOOGLE_PLAY_CANCEL_SANDBOX=True,
        )
    )

    assert adapter.enabled and adapter.sandbox_enabled
    assert adapter.package_name == "com.nutreeai.mobile.staging"
    assert adapter._access_token._signer.scopes == [ANDROID_PUBLISHER_SCOPE]


def test_the_service_account_file_wins_over_the_json_setting(
    tmp_path, service_account_json
):
    key_file = tmp_path / "firebase.json"
    key_file.write_text(service_account_json)

    adapter = GooglePlaySubscriptionAdapter.from_settings(
        _settings(FIREBASE_CREDENTIALS=str(key_file), FIREBASE_SERVICE_ACCOUNT_JSON="{")
    )

    assert adapter.enabled


def test_it_is_on_without_any_switch(service_account_json):
    adapter = GooglePlaySubscriptionAdapter.from_settings(
        _settings(FIREBASE_SERVICE_ACCOUNT_JSON=service_account_json)
    )

    assert adapter.switched_on and adapter.enabled


def test_the_package_defaults_to_the_production_app():
    from src.infra.config.settings import Settings

    assert Settings.model_fields["GOOGLE_PLAY_PACKAGE_NAME"].default == (
        "com.nutreeai.mobile"
    )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"FIREBASE_SERVICE_ACCOUNT_JSON": ""},
        {"FIREBASE_SERVICE_ACCOUNT_JSON": "not json"},
        {"FIREBASE_SERVICE_ACCOUNT_JSON": json.dumps({"type": "authorized_user"})},
        {"FIREBASE_SERVICE_ACCOUNT_JSON": json.dumps({"type": "service_account"})},
        {"FIREBASE_CREDENTIALS": "/nonexistent/firebase.json"},
    ],
    ids=["unset", "empty", "not-json", "not-a-service-account", "no-key", "no-file"],
)
def test_without_a_usable_service_account_it_stays_off(caplog, fields):
    with caplog.at_level(logging.ERROR):
        adapter = GooglePlaySubscriptionAdapter.from_settings(_settings(**fields))

    assert not adapter.enabled and adapter.switched_on
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and all(r.exc_info is None for r in errors)


# Sentry sends a traceback's frame variables, so the loader must never let
# one out: no settings, no file content and no key in any log or frame.
# A plain sentinel: a PEM-looking literal would trip secret scanners.
SECRET = "sentinel-key-material-that-must-never-be-logged"


def _assert_logged_safely(caplog) -> None:
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and all(r.exc_info is None for r in caplog.records)
    assert "super-secret" not in caplog.text and "Settings" not in caplog.text


def test_a_key_file_that_is_not_utf8_is_caught_and_logged_safely(tmp_path, caplog):
    key_file = tmp_path / "firebase.json"
    key_file.write_bytes(b"\xff\xfe" + json.dumps({"private_key": SECRET}).encode())

    with caplog.at_level(logging.ERROR):
        adapter = GooglePlaySubscriptionAdapter.from_settings(
            _settings(FIREBASE_CREDENTIALS=str(key_file))
        )

    assert not adapter.enabled
    _assert_logged_safely(caplog)
    assert "file unreadable for Google Play: error=UnicodeDecodeError" in caplog.text


@pytest.mark.parametrize("value", [12345, b"bytes", ["list"]])
def test_a_service_account_setting_that_is_not_text_is_caught(caplog, value):
    with caplog.at_level(logging.ERROR):
        adapter = GooglePlaySubscriptionAdapter.from_settings(
            _settings(FIREBASE_SERVICE_ACCOUNT_JSON=value)
        )

    assert not adapter.enabled
    _assert_logged_safely(caplog)


def test_a_key_the_library_chokes_on_is_caught_and_logged_safely(
    caplog, service_account_json
):
    failure = RuntimeError(f"cannot parse {SECRET}")
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "src.infra.adapters.google_play_auth.service_account.Credentials"
            ".from_service_account_info",
            side_effect=failure,
        ),
    ):
        adapter = GooglePlaySubscriptionAdapter.from_settings(
            _settings(FIREBASE_SERVICE_ACCOUNT_JSON=service_account_json)
        )

    assert not adapter.enabled
    _assert_logged_safely(caplog)
    assert "error=RuntimeError" in caplog.text


def test_a_malformed_key_is_reported_without_its_content(caplog):
    broken = json.dumps(
        {
            "type": "service_account",
            "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-key\n",
            "client_email": "a@b.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    with caplog.at_level(logging.ERROR):
        adapter = GooglePlaySubscriptionAdapter.from_settings(
            _settings(FIREBASE_SERVICE_ACCOUNT_JSON=broken)
        )

    assert not adapter.enabled
    assert "not-a-key" not in caplog.text and "PRIVATE KEY" not in caplog.text


@pytest.mark.parametrize("package_name", ["", "nutree", "com.nutree ai", "1com.x"])
def test_a_package_name_that_is_no_application_id_keeps_it_off(
    caplog, service_account_json, package_name
):
    with patch(f"{ADAPTER}.load_access_token") as load:
        adapter = GooglePlaySubscriptionAdapter.from_settings(
            _settings(
                FIREBASE_SERVICE_ACCOUNT_JSON=service_account_json,
                GOOGLE_PLAY_PACKAGE_NAME=package_name,
            )
        )

    assert not adapter.enabled
    load.assert_not_called()
    assert "GOOGLE_PLAY_PACKAGE_NAME" in caplog.text


# --- The access token ---


class FakeSigner:
    """The google-auth credential calls the token source makes."""

    def __init__(self, *, valid=False, error=None, delay=0.0, issues="access-1"):
        self.valid = valid
        self.token = issues if valid else None
        self.refreshed_with = []
        self._error, self._delay, self._issues = error, delay, issues

    def refresh(self, request):
        self.refreshed_with.append(request)
        time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        self.token, self.valid = self._issues, True


@pytest.mark.asyncio
async def test_a_valid_token_is_reused():
    signer = FakeSigner(valid=True)

    assert await ServiceAccountAccessToken(signer, 1.0).get() == "access-1"
    assert signer.refreshed_with == []


@pytest.mark.asyncio
async def test_a_stale_token_is_refreshed_once_through_the_bounded_request():
    signer = FakeSigner()
    request = object()
    source = ServiceAccountAccessToken(signer, 1.0, request=request)

    assert await source.get() == "access-1"
    assert await source.get() == "access-1"
    assert signer.refreshed_with == [request]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signer", "error"),
    [
        (
            FakeSigner(error=google_auth_exceptions.RefreshError("invalid_grant")),
            PlayBillingConfigurationError,
        ),
        (
            FakeSigner(
                error=google_auth_exceptions.RefreshError("busy", retryable=True)
            ),
            PlayBillingUnavailableError,
        ),
        (
            FakeSigner(error=google_auth_exceptions.TransportError("down")),
            PlayBillingUnavailableError,
        ),
        (FakeSigner(delay=0.3), PlayBillingUnavailableError),
        (FakeSigner(issues=None), PlayBillingConfigurationError),
    ],
    ids=["refused", "retryable", "transport", "slow", "no-token"],
)
async def test_a_token_failure_is_typed(signer, error):
    with pytest.raises(error):
        await ServiceAccountAccessToken(signer, 0.05).get()


def test_every_token_request_carries_the_fixed_timeout():
    with patch.object(GoogleAuthRequest, "__call__", MagicMock()) as call:
        _BoundedRequest(1.5)("https://oauth2.googleapis.com/token", timeout=120)

    assert call.call_args.kwargs["timeout"] == 1.5
