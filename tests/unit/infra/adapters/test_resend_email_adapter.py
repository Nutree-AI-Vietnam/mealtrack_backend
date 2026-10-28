"""Tests for ResendEmailAdapter."""

from unittest.mock import MagicMock, patch

import pytest

from src.infra.adapters.resend_email_adapter import ResendEmailAdapter


@pytest.fixture
def mock_settings():
    with patch("src.infra.adapters.resend_email_adapter.get_settings") as mock:
        settings = MagicMock()
        settings.RESEND_API_KEY = "re_test_123"
        settings.EMAIL_FROM = "Nutree <test@nutree.app>"
        settings.EMAIL_REPLY_TO = "support@nutreeai.com"
        settings.EMAIL_ENABLED = True
        mock.return_value = settings
        yield settings


@pytest.mark.asyncio
async def test_send_email_success(mock_settings):
    with patch("src.infra.adapters.resend_email_adapter.resend") as mock_resend:
        mock_resend.Emails.send.return_value = {"id": "msg_123"}

        adapter = ResendEmailAdapter()
        result = await adapter.send_email(
            to="user@example.com",
            subject="Welcome!",
            html_body="<p>Hello</p>",
            tags=["welcome"],
        )

        assert result.success is True
        assert result.message_id == "msg_123"
        mock_resend.Emails.send.assert_called_once()
        assert mock_resend.Emails.send.call_args.args[0]["reply_to"] == (
            "support@nutreeai.com"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_to", [None, ""])
async def test_send_email_without_reply_to_omits_it(mock_settings, reply_to):
    mock_settings.EMAIL_REPLY_TO = reply_to
    with patch("src.infra.adapters.resend_email_adapter.resend") as mock_resend:
        mock_resend.Emails.send.return_value = {"id": "msg_123"}

        adapter = ResendEmailAdapter()
        await adapter.send_email(
            to="user@example.com",
            subject="Welcome!",
            html_body="<p>Hello</p>",
        )

        assert "reply_to" not in mock_resend.Emails.send.call_args.args[0]


@pytest.mark.asyncio
async def test_send_email_disabled_does_not_log_recipient_or_subject(
    mock_settings, caplog
):
    mock_settings.EMAIL_ENABLED = False

    adapter = ResendEmailAdapter()
    with caplog.at_level("INFO"):
        result = await adapter.send_email(
            to="user@example.com",
            subject="Welcome!",
            html_body="<p>Hello</p>",
        )

    assert result.success is True
    assert result.message_id == "disabled"
    assert "user@example.com" not in caplog.text
    assert "Welcome!" not in caplog.text


@pytest.mark.asyncio
async def test_send_email_api_error_does_not_log_recipient_or_subject(
    mock_settings, caplog
):
    with patch("src.infra.adapters.resend_email_adapter.resend") as mock_resend:
        mock_resend.Emails.send.side_effect = Exception("API error")

        adapter = ResendEmailAdapter()
        with caplog.at_level("ERROR"):
            result = await adapter.send_email(
                to="user@example.com",
                subject="Welcome!",
                html_body="<p>Hello</p>",
            )

        assert result.success is False
        assert "API error" in result.error
        assert "user@example.com" not in caplog.text
        assert "Welcome!" not in caplog.text
        assert "API error" not in caplog.text


@pytest.mark.asyncio
async def test_send_email_no_api_key(mock_settings):
    mock_settings.RESEND_API_KEY = None

    adapter = ResendEmailAdapter()
    result = await adapter.send_email(
        to="user@example.com",
        subject="Welcome!",
        html_body="<p>Hello</p>",
    )

    assert result.success is False
    assert result.error == "API key not configured"
