"""RevenueCat webhook auth precedence, settings wiring and delivery metrics."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi import HTTPException

from src.api import base_dependencies
from src.api.routes.v1 import webhooks
from src.api.routes.v1.webhooks import revenuecat_webhook
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter

_SETTINGS = "src.api.routes.v1.webhooks.settings"


def _credentials(*, auth_header: str | None, secret: str | None):
    return patch.multiple(
        _SETTINGS,
        REVENUECAT_WEBHOOK_AUTH_HEADER=auth_header,
        REVENUECAT_WEBHOOK_SECRET=secret,
    )


@pytest.fixture
def unparseable_request():
    """A request whose body fails to parse: a 400 proves auth already passed."""
    request = MagicMock()
    request.json = AsyncMock(side_effect=ValueError("not json"))
    return request


async def _status_for(request, authorization: str | None) -> int:
    with pytest.raises(HTTPException) as exc_info:
        await revenuecat_webhook(request, authorization=authorization)
    return exc_info.value.status_code


@pytest.mark.asyncio
class TestWebhookAuthPrecedence:
    async def test_auth_header_takes_precedence_over_secret(self, unparseable_request):
        with _credentials(auth_header="Bearer header-value", secret="secret-value"):
            assert await _status_for(unparseable_request, "secret-value") == 401
            assert await _status_for(unparseable_request, "Bearer header-value") == 400

    async def test_auth_header_rejection_detail(self, unparseable_request):
        with _credentials(auth_header="Bearer header-value", secret="secret-value"):
            with pytest.raises(HTTPException) as exc_info:
                await revenuecat_webhook(unparseable_request, authorization=None)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid authorization header"

    async def test_auth_header_comparison_ignores_surrounding_whitespace(
        self, unparseable_request
    ):
        with _credentials(auth_header=" Bearer header-value ", secret=None):
            assert (
                await _status_for(unparseable_request, "Bearer header-value  ") == 400
            )

    async def test_secret_used_when_auth_header_unset(self, unparseable_request):
        with _credentials(auth_header=None, secret="secret-value"):
            assert await _status_for(unparseable_request, "secret-value") == 400
            assert await _status_for(unparseable_request, "wrong") == 401
            assert await _status_for(unparseable_request, None) == 401

    async def test_empty_auth_header_falls_back_to_secret(self, unparseable_request):
        with _credentials(auth_header="", secret="secret-value"):
            assert await _status_for(unparseable_request, "secret-value") == 400

    @pytest.mark.parametrize(
        ("auth_header", "secret"),
        [("Bearer header-value", None), (None, "secret-value")],
    )
    async def test_non_ascii_credentials_are_rejected_with_401(
        self, unparseable_request, auth_header, secret
    ):
        # Starlette decodes header bytes as latin-1: b"\xe9" arrives as "\xe9".
        with (
            _credentials(auth_header=auth_header, secret=secret),
            patch.object(webhooks, "increment_metric") as metric,
        ):
            assert await _status_for(unparseable_request, "Bearer \xe9") == 401
        metric.assert_called_with(
            "webhook.revenuecat.non_2xx", attributes={"status": "401"}
        )

    async def test_non_ascii_configured_secret_still_matches(self, unparseable_request):
        with _credentials(auth_header="Bearer s\u00e9cret", secret=None):
            assert await _status_for(unparseable_request, "Bearer s\u00e9cret") == 400
            assert await _status_for(unparseable_request, "Bearer secret") == 401

    async def test_unconfigured_webhook_rejects_with_503(self, unparseable_request):
        with _credentials(auth_header=None, secret=None):
            assert await _status_for(unparseable_request, "anything") == 503

    async def test_route_reads_settings_not_process_env(
        self, unparseable_request, monkeypatch
    ):
        monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH_HEADER", "from-env")
        monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", "from-env")
        with _credentials(auth_header=None, secret="from-settings"):
            assert await _status_for(unparseable_request, "from-env") == 401
            assert await _status_for(unparseable_request, "from-settings") == 400


@pytest.mark.asyncio
class TestWebhookDeliveryMetrics:
    async def test_success_counts_received_only(self):
        with (
            patch.object(webhooks, "increment_metric") as metric,
            patch.object(
                webhooks,
                "_process_revenuecat_webhook",
                new=AsyncMock(return_value={"status": "success"}),
            ),
        ):
            result = await revenuecat_webhook(MagicMock(), authorization="x")

        assert result == {"status": "success"}
        assert metric.call_args_list == [call("webhook.revenuecat.received")]

    async def test_auth_failure_counts_non_2xx_with_status(self, unparseable_request):
        with (
            _credentials(auth_header=None, secret="secret-value"),
            patch.object(webhooks, "increment_metric") as metric,
        ):
            assert await _status_for(unparseable_request, "wrong") == 401

        assert metric.call_args_list == [
            call("webhook.revenuecat.received"),
            call("webhook.revenuecat.non_2xx", attributes={"status": "401"}),
        ]

    async def test_unexpected_error_counts_as_500_and_propagates(self):
        with (
            patch.object(webhooks, "increment_metric") as metric,
            patch.object(
                webhooks,
                "_process_revenuecat_webhook",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            with pytest.raises(RuntimeError, match="db down"):
                await revenuecat_webhook(MagicMock(), authorization="x")

        assert metric.call_args_list == [
            call("webhook.revenuecat.received"),
            call("webhook.revenuecat.non_2xx", attributes={"status": "500"}),
        ]


class TestRevenueCatApiKeyFromSettings:
    def test_adapter_defaults_to_settings_key(self, monkeypatch):
        monkeypatch.setenv("REVENUECAT_SECRET_API_KEY", "from-env")
        with patch(
            "src.infra.adapters.revenuecat_adapter.settings.REVENUECAT_SECRET_API_KEY",
            "from-settings",
        ):
            assert RevenueCatAdapter().api_key == "from-settings"
            assert RevenueCatAdapter(api_key="explicit").api_key == "explicit"

    def test_adapter_key_empty_when_unset(self):
        with patch(
            "src.infra.adapters.revenuecat_adapter.settings.REVENUECAT_SECRET_API_KEY",
            None,
        ):
            assert RevenueCatAdapter().api_key == ""

    def test_subscription_service_singleton_uses_settings_key(self, monkeypatch):
        monkeypatch.setattr(base_dependencies, "_subscription_service", None)
        monkeypatch.setattr(
            base_dependencies.settings, "REVENUECAT_SECRET_API_KEY", "from-settings"
        )

        service = base_dependencies.get_subscription_service()

        assert isinstance(service, RevenueCatAdapter)
        assert service.api_key == "from-settings"
