"""Response schemas for subscription access checks and their monitoring."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MyEntitlementsResponse(BaseModel):
    """Database access decision for the signed-in user.

    product_id, will_renew and provider describe the granting subscription
    row with the latest deadline (a lifetime grant wins). They are null when
    has_access is false.
    """

    has_access: bool
    status: Literal["none", "trial", "active", "grace_period", "expired"]
    expires_at: datetime | None
    product_id: str | None = None
    will_renew: bool | None = None
    provider: str | None = None


class SubscriptionAccessMarkerCountsResponse(BaseModel):
    """Provider markers on live users' RevenueCat rows (RevenueCat event time)."""

    sync_required_users: int
    stale_sync_required_users: int = Field(
        description="Users whose sync-required marker is older than "
        "thresholds.sync_required_minutes"
    )
    refund_pending_rows: int
    stale_refund_pending_rows: int = Field(
        description="Rows whose refund-pending marker is older than "
        "thresholds.refund_pending_minutes"
    )
    transfer_source_rows: int
    oldest_marker_age_seconds: int | None = Field(
        description="Age of the oldest marker of any type; null when none"
    )


class SubscriptionAccessAlertThresholdsResponse(BaseModel):
    sync_required_minutes: float
    sync_required_users: int
    refund_pending_minutes: float
    oldest_marker_hours: float


class SubscriptionAccessHealthReasonResponse(BaseModel):
    code: Literal[
        "sync_required_backlog", "refund_pending_stale", "oldest_marker_stale"
    ]
    message: str


class SubscriptionAccessHealthResponse(BaseModel):
    """200 with status ok, or 503 with status degraded and the reasons."""

    status: Literal["ok", "degraded"]
    checked_at: datetime
    counts: SubscriptionAccessMarkerCountsResponse
    thresholds: SubscriptionAccessAlertThresholdsResponse
    reasons: list[SubscriptionAccessHealthReasonResponse]
