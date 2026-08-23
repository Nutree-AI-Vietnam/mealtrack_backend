from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.domain.models.outbox_status import OutboxEvent, OutboxStatus
from src.domain.ports.outbox_handler_port import (
    OutboxEventContext,
    OutboxHandlerResult,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.handlers import (
    AffiliateWebhookHandler,
    PushNotificationQueueHandler,
    TelemetryHandler,
    create_default_handler_registry,
)
from src.infra.services.outbox_dispatch_engine import OutboxDispatchEngine
from src.infra.services.outbox_handler_registry import OutboxHandlerRegistry

# ---------------------------------------------------------------------------
# OutboxHandlerRegistry Tests
# ---------------------------------------------------------------------------


class TestOutboxHandlerRegistry:
    def test_register_and_get(self):
        registry = OutboxHandlerRegistry()
        handler = MagicMock()
        handler.handle = AsyncMock()

        registry.register("order.created", handler)
        assert registry.get("order.created") is handler
        assert registry.has("order.created") is True
        assert registry.registered_event_types == ["order.created"]

    def test_register_invalid_inputs(self):
        registry = OutboxHandlerRegistry()
        with pytest.raises(ValueError, match="event_type cannot be empty"):
            registry.register("", MagicMock(handle=AsyncMock()))

        with pytest.raises(
            TypeError, match="Handler must implement OutboxEventHandler"
        ):
            registry.register("valid.type", object())  # type: ignore[arg-type]

    def test_unregister(self):
        registry = OutboxHandlerRegistry()
        handler = MagicMock(handle=AsyncMock())
        registry.register("test.event", handler)
        assert registry.has("test.event") is True

        registry.unregister("test.event")
        assert registry.has("test.event") is False
        assert registry.get("test.event") is None

    @pytest.mark.asyncio
    async def test_get_or_fallback_returns_permanent_failure_for_unknown_event(self):
        registry = OutboxHandlerRegistry()
        fallback = registry.get_or_fallback("unregistered.event")
        assert fallback is not None

        context = OutboxEventContext(
            outbox_id="out-123",
            event_id="evt-123",
            event_type="unregistered.event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await fallback.handle({}, context)
        assert res.success is False
        assert res.is_transient is False
        assert res.error_type == "UnregisteredEventType"
        assert "unregistered.event" in (res.error_message or "")


# ---------------------------------------------------------------------------
# AffiliateWebhookHandler Tests
# ---------------------------------------------------------------------------


class TestAffiliateWebhookHandler:
    @pytest.mark.asyncio
    async def test_affiliate_success(self):
        adapter = MagicMock()
        adapter.send_event = AsyncMock(return_value=True)
        handler = AffiliateWebhookHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="affiliate_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle({"referral_code": "AFF123"}, context)
        assert res.success is True
        adapter.send_event.assert_awaited_once_with({"referral_code": "AFF123"})

    @pytest.mark.asyncio
    async def test_affiliate_delivery_failure_transient(self):
        adapter = MagicMock()
        adapter.send_event = AsyncMock(return_value=False)
        handler = AffiliateWebhookHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="affiliate_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle({"referral_code": "AFF123"}, context)
        assert res.success is False
        assert res.is_transient is True
        assert res.error_type == "AffiliateDeliveryError"

    @pytest.mark.asyncio
    async def test_affiliate_http_4xx_permanent_failure(self):
        adapter = MagicMock()
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(400, request=request)
        adapter.send_event = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Bad Request", request=request, response=response
            )
        )
        handler = AffiliateWebhookHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="affiliate_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle({"referral_code": "BAD"}, context)
        assert res.success is False
        assert res.is_transient is False
        assert res.error_type == "HTTPClientError"
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_affiliate_http_5xx_transient_failure(self):
        adapter = MagicMock()
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(503, request=request)
        adapter.send_event = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Unavailable", request=request, response=response
            )
        )
        handler = AffiliateWebhookHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="affiliate_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle({"referral_code": "OK"}, context)
        assert res.success is False
        assert res.is_transient is True
        assert res.error_type == "HTTPServerError"
        assert res.status_code == 503

    @pytest.mark.asyncio
    async def test_affiliate_timeout_transient_failure(self):
        adapter = MagicMock()
        adapter.send_event = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
        handler = AffiliateWebhookHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="affiliate_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle({"referral_code": "OK"}, context)
        assert res.success is False
        assert res.is_transient is True
        assert res.error_type == "TimeoutException"

    @pytest.mark.asyncio
    async def test_affiliate_invalid_payload_permanent_failure(self):
        handler = AffiliateWebhookHandler(MagicMock())
        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="affiliate_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle("invalid", context)  # type: ignore[arg-type]
        assert res.success is False
        assert res.is_transient is False
        assert res.error_type == "InvalidPayload"


# ---------------------------------------------------------------------------
# PushNotificationQueueHandler Tests
# ---------------------------------------------------------------------------


class TestPushNotificationQueueHandler:
    @pytest.mark.asyncio
    async def test_push_queue_success(self):
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        publisher._queue_name = "mealtrack-events"
        handler = PushNotificationQueueHandler(publisher)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="push_notification",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle(
            {"topic": "news", "title": "MealTrack", "body": "Log your lunch"},
            context,
        )
        assert res.success is True
        assert res.metadata.get("destination") == "cloudflare_queue"
        publisher.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_push_queue_transient_failure(self):
        from src.infra.adapters.cloudflare_queue_publisher import (
            CloudflareQueueTransientError,
        )

        publisher = MagicMock()
        publisher.publish = AsyncMock(
            side_effect=CloudflareQueueTransientError("Rate limited")
        )
        publisher._queue_name = "mealtrack-events"
        handler = PushNotificationQueueHandler(publisher)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="push_notification",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle(
            {"tokens": ["tok-1"], "title": "MealTrack", "body": "Dinner time"},
            context,
        )
        assert res.success is False
        assert res.is_transient is True

    @pytest.mark.asyncio
    async def test_push_queue_permanent_failure(self):
        from src.infra.adapters.cloudflare_queue_publisher import (
            CloudflareQueuePermanentError,
        )

        publisher = MagicMock()
        publisher.publish = AsyncMock(
            side_effect=CloudflareQueuePermanentError("Bad auth")
        )
        publisher._queue_name = "mealtrack-events"
        handler = PushNotificationQueueHandler(publisher)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="push_notification",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle(
            {"user_id": "u-123", "title": "MealTrack", "body": "Dinner time"},
            context,
        )
        assert res.success is False
        assert res.is_transient is False


# ---------------------------------------------------------------------------
# TelemetryHandler Tests
# ---------------------------------------------------------------------------


class TestTelemetryHandler:
    @pytest.mark.asyncio
    async def test_telemetry_success(self):
        adapter = MagicMock()
        adapter.capture = AsyncMock()
        handler = TelemetryHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="telemetry_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle(
            {
                "distinct_id": "user-456",
                "event": "meal.logged",
                "properties": {"calories": 500},
            },
            context,
        )
        assert res.success is True
        adapter.capture.assert_awaited_once_with(
            distinct_id="user-456",
            event="meal.logged",
            properties={"calories": 500},
        )

    @pytest.mark.asyncio
    async def test_telemetry_missing_user_id_permanent_failure(self):
        adapter = MagicMock()
        handler = TelemetryHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="telemetry_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle({"event": "meal.logged"}, context)
        assert res.success is False
        assert res.is_transient is False
        assert res.error_type == "ValidationError"

    @pytest.mark.asyncio
    async def test_telemetry_invalid_properties_permanent_failure(self):
        adapter = MagicMock()
        handler = TelemetryHandler(adapter)

        context = OutboxEventContext(
            outbox_id="1",
            event_id="e1",
            event_type="telemetry_event",
            retry_count=0,
            created_at_iso="2026-08-22T00:00:00Z",
        )
        res = await handler.handle(
            {"user_id": "u1", "properties": "not-a-dict"},
            context,
        )
        assert res.success is False
        assert res.is_transient is False
        assert res.error_type == "ValidationError"


# ---------------------------------------------------------------------------
# Default Registry Factory Tests
# ---------------------------------------------------------------------------


class TestCreateDefaultHandlerRegistry:
    def test_default_registry_has_standard_routes(self):
        registry = create_default_handler_registry()
        expected_routes = [
            "affiliate_event",
            "affiliate_webhook",
            "push_notification",
            "scheduled_push",
            "telemetry_event",
            "analytics.event",
            "posthog.capture",
            "firebase_account_cleanup",
            "notification_reschedule",
        ]
        for route in expected_routes:
            assert registry.has(route) is True


# ---------------------------------------------------------------------------
# OutboxDispatchEngine Tests
# ---------------------------------------------------------------------------


class TestOutboxDispatchEngine:
    @pytest.fixture
    def mock_db_session_factory(self):
        """Creates a mock async session factory simulating short transactions."""

        def _factory():
            session = MagicMock()
            begin_cm = AsyncMock()
            begin_cm.__aenter__.return_value = session
            begin_cm.__aexit__.return_value = False
            session.begin = MagicMock(return_value=begin_cm)

            cm = AsyncMock()
            cm.__aenter__.return_value = session
            cm.__aexit__.return_value = False
            return cm

        return _factory

    @pytest.mark.asyncio
    async def test_run_once_empty_queue(self, mock_db_session_factory):
        registry = OutboxHandlerRegistry()
        engine = OutboxDispatchEngine(
            registry=registry,
            session_factory=mock_db_session_factory,
        )

        with patch(
            "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.claim_due_records",
            new_callable=AsyncMock,
            return_value=[],
        ):
            stats = await engine.run_once()

        assert stats == {"claimed": 0, "completed": 0, "retried": 0, "dead_letter": 0}

    @pytest.mark.asyncio
    async def test_run_once_successful_dispatch(self, mock_db_session_factory):
        registry = OutboxHandlerRegistry()
        handler = MagicMock()
        handler.handle = AsyncMock(return_value=OutboxHandlerResult.ok())
        registry.register("affiliate_event", handler)

        engine = OutboxDispatchEngine(
            registry=registry,
            session_factory=mock_db_session_factory,
            worker_id="test-worker",
        )

        now = utc_now()
        claimed_event = OutboxEvent(
            id="outbox-1",
            event_id="evt-1",
            event_type="affiliate_event",
            payload={"code": "XYZ"},
            status=OutboxStatus.IN_PROGRESS,
            retry_count=0,
            max_retries=5,
            next_retry_at=now,
            lease_owner="test-worker",
            lease_expires_at=now,
            created_at=now,
            updated_at=now,
        )

        with (
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.claim_due_records",
                new_callable=AsyncMock,
                return_value=[claimed_event],
            ),
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.mark_completed",
                new_callable=AsyncMock,
            ) as mock_mark_completed,
            patch(
                "src.infra.services.outbox_dispatch_engine.increment_metric"
            ) as mock_metric,
            patch("src.infra.services.outbox_dispatch_engine.log_event") as mock_log,
        ):
            stats = await engine.run_once()

        assert stats == {"claimed": 1, "completed": 1, "retried": 0, "dead_letter": 0}
        mock_mark_completed.assert_awaited_once_with("outbox-1")
        mock_metric.assert_called_with(
            "outbox.processed",
            attributes={
                "component": "outbox_dispatcher",
                "event_type": "affiliate_event",
                "status": "completed",
            },
        )
        mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_once_transient_failure_reschedules(
        self, mock_db_session_factory
    ):
        registry = OutboxHandlerRegistry()
        handler = MagicMock()
        handler.handle = AsyncMock(
            return_value=OutboxHandlerResult.transient_failure(
                "503 Service Unavailable",
                error_type="HTTPServerError",
                status_code=503,
            )
        )
        registry.register("affiliate_event", handler)

        engine = OutboxDispatchEngine(
            registry=registry,
            session_factory=mock_db_session_factory,
        )

        now = utc_now()
        claimed_event = OutboxEvent(
            id="outbox-2",
            event_id="evt-2",
            event_type="affiliate_event",
            payload={"code": "XYZ"},
            status=OutboxStatus.IN_PROGRESS,
            retry_count=1,
            max_retries=5,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )

        with (
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.claim_due_records",
                new_callable=AsyncMock,
                return_value=[claimed_event],
            ),
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.record_failure",
                new_callable=AsyncMock,
                return_value=False,  # Rescheduled as PENDING
            ) as mock_record_failure,
            patch(
                "src.infra.services.outbox_dispatch_engine.increment_metric"
            ) as mock_metric,
        ):
            stats = await engine.run_once()

        assert stats == {"claimed": 1, "completed": 0, "retried": 1, "dead_letter": 0}
        mock_record_failure.assert_awaited_once()
        mock_metric.assert_called_with(
            "outbox.retried",
            attributes={
                "component": "outbox_dispatcher",
                "event_type": "affiliate_event",
                "status": "pending",
            },
        )

    @pytest.mark.asyncio
    async def test_run_once_permanent_failure_moves_to_dlq(
        self, mock_db_session_factory
    ):
        registry = OutboxHandlerRegistry()
        handler = MagicMock()
        handler.handle = AsyncMock(
            return_value=OutboxHandlerResult.permanent_failure(
                "400 Bad Request",
                error_type="HTTPClientError",
                status_code=400,
            )
        )
        registry.register("affiliate_event", handler)

        engine = OutboxDispatchEngine(
            registry=registry,
            session_factory=mock_db_session_factory,
        )

        now = utc_now()
        claimed_event = OutboxEvent(
            id="outbox-3",
            event_id="evt-3",
            event_type="affiliate_event",
            payload={"code": "INVALID"},
            status=OutboxStatus.IN_PROGRESS,
            retry_count=0,
            max_retries=5,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )

        with (
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.claim_due_records",
                new_callable=AsyncMock,
                return_value=[claimed_event],
            ),
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.record_failure",
                new_callable=AsyncMock,
                return_value=True,  # Moved to DLQ
            ) as mock_record_failure,
            patch(
                "src.infra.services.outbox_dispatch_engine.increment_metric"
            ) as mock_metric,
            patch(
                "src.infra.services.outbox_dispatch_engine.capture_message"
            ) as mock_sentry,
        ):
            stats = await engine.run_once()

        assert stats == {"claimed": 1, "completed": 0, "retried": 0, "dead_letter": 1}
        mock_record_failure.assert_awaited_once()
        mock_metric.assert_called_with(
            "outbox.dead_letter",
            attributes={
                "component": "outbox_dispatcher",
                "event_type": "affiliate_event",
                "status": "failed_dead_letter",
            },
        )
        mock_sentry.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_once_unregistered_event_type_dlq(self, mock_db_session_factory):
        registry = OutboxHandlerRegistry()  # Empty registry
        engine = OutboxDispatchEngine(
            registry=registry,
            session_factory=mock_db_session_factory,
        )

        now = utc_now()
        claimed_event = OutboxEvent(
            id="outbox-4",
            event_id="evt-4",
            event_type="unknown.event.type",
            payload={},
            status=OutboxStatus.IN_PROGRESS,
            retry_count=0,
            max_retries=5,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )

        with (
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.claim_due_records",
                new_callable=AsyncMock,
                return_value=[claimed_event],
            ),
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.record_failure",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_record_failure,
            patch("src.infra.services.outbox_dispatch_engine.capture_message"),
        ):
            stats = await engine.run_once()

        assert stats["dead_letter"] == 1
        # The result passed to record_failure must be a permanent failure
        args, _ = mock_record_failure.call_args
        res: OutboxHandlerResult = args[1]
        assert res.is_transient is False
        assert res.error_type == "UnregisteredEventType"

    @pytest.mark.asyncio
    async def test_run_once_handler_exception_handled_as_transient(
        self, mock_db_session_factory
    ):
        registry = OutboxHandlerRegistry()
        handler = MagicMock()
        handler.handle = AsyncMock(side_effect=RuntimeError("Unexpected crash"))
        registry.register("crash.event", handler)

        engine = OutboxDispatchEngine(
            registry=registry,
            session_factory=mock_db_session_factory,
        )

        now = utc_now()
        claimed_event = OutboxEvent(
            id="outbox-5",
            event_id="evt-5",
            event_type="crash.event",
            payload={},
            status=OutboxStatus.IN_PROGRESS,
            retry_count=0,
            max_retries=5,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )

        with (
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.claim_due_records",
                new_callable=AsyncMock,
                return_value=[claimed_event],
            ),
            patch(
                "src.infra.services.outbox_dispatch_engine.AsyncOutboxRepository.record_failure",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_record_failure,
        ):
            stats = await engine.run_once()

        assert stats["retried"] == 1
        args, _ = mock_record_failure.call_args
        res: OutboxHandlerResult = args[1]
        assert res.is_transient is True
        assert res.error_type == "RuntimeError"

    @pytest.mark.asyncio
    async def test_session_factory_none_raises_runtime_error(self):
        engine = OutboxDispatchEngine(
            registry=OutboxHandlerRegistry(),
            session_factory=None,
        )
        # Explicitly set _session_factory to None
        engine._session_factory = None  # type: ignore[assignment]
        with pytest.raises(
            RuntimeError, match="Async database session factory is not initialized"
        ):
            await engine.run_once()
