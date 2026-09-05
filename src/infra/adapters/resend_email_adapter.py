"""Resend email adapter implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

import resend

from src.infra.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class EmailResult:
    """Result of an email send operation."""

    success: bool
    message_id: str | None = None
    error: str | None = None


class ResendEmailAdapter:
    """Resend SDK wrapper for sending emails."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.RESEND_API_KEY
        self._from_email = settings.EMAIL_FROM
        self._enabled = settings.EMAIL_ENABLED

        if self._api_key:
            resend.api_key = self._api_key

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        tags: list[str] | None = None,
    ) -> EmailResult:
        """Send email via Resend API."""
        if not self._enabled:
            logger.info("Email disabled, skipping send")
            return EmailResult(success=True, message_id="disabled")

        if not self._api_key:
            logger.warning("RESEND_API_KEY not configured")
            return EmailResult(success=False, error="API key not configured")

        try:
            result = await asyncio.to_thread(
                resend.Emails.send,
                {
                    "from": self._from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html_body,
                    "tags": [{"name": tag, "value": "true"} for tag in (tags or [])],
                },
            )
            message_id = result.get("id") if isinstance(result, dict) else str(result)
            if not message_id:
                logger.warning("Email sent but no message_id returned")
            logger.info("Email sent successfully (id=%s)", message_id)
            return EmailResult(success=True, message_id=message_id)

        except Exception as e:
            logger.error("Failed to send email: error_type=%s", type(e).__name__)
            return EmailResult(success=False, error=str(e))
