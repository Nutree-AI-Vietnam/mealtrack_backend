"""Response schemas for the subscription access support API (admin only)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SupportUserResponse(BaseModel):
    id: str = Field(description="Internal user id")
    firebase_uid: str | None = Field(
        description="Firebase uid (RevenueCat app_user_id); a tombstone once deleted"
    )
    email: str | None
    is_active: bool
    deleted_at: datetime | None


class SupportSubscriptionRowResponse(BaseModel):
    id: str
    provider: str
    status: str
    product_id: str
    platform: str
    purchased_at: datetime
    expires_at: datetime | None
    grace_period_expires_at: datetime | None
    provider_snapshot_at: datetime | None
    provider_sync_required_at: datetime | None
    provider_refund_pending_at: datetime | None
    provider_transfer_source_at: datetime | None
    is_sandbox: bool


class SupportAccessDecisionResponse(BaseModel):
    """What GET /v1/me/entitlements answers for these rows, or why it cannot.

    ``outcome`` unresolved means the policy cannot decide (the user's GET
    answers 503 ENTITLEMENTS_UNAVAILABLE): every entitlement field is null
    and ``unresolved_reasons`` lists sync_required, refund_pending,
    transfer_source or unverified_grant.
    """

    outcome: Literal["granted", "denied", "unresolved"]
    has_access: bool | None
    status: Literal["none", "trial", "active", "grace_period", "expired"] | None
    expires_at: datetime | None
    product_id: str | None
    will_renew: bool | None
    provider: str | None
    unresolved_reasons: list[str]


class SupportAccessFlagsResponse(BaseModel):
    subscription_access_enabled: bool = Field(
        description="SUBSCRIPTION_ACCESS_ENABLED: GET /v1/me/entitlements decides"
    )
    subscription_enforcement_enabled: bool = Field(
        description="SUBSCRIPTION_ENFORCEMENT_ENABLED, as set"
    )
    enforcement_active: bool = Field(
        description="Both flags on: paid routes answer 402 without access"
    )


class SupportLiveEntitlementResponse(BaseModel):
    """RevenueCat's 'standard' entitlement, normalized as a resync would store it."""

    has_access: bool
    status: Literal["active", "cancelled", "billing_issue", "expired"]
    product_id: str
    platform: str
    purchased_at: datetime
    expires_at: datetime | None
    grace_period_expires_at: datetime | None
    period_type: str
    is_sandbox: bool
    will_renew: bool


class SupportLiveReadResponse(BaseModel):
    """A read-only RevenueCat read; nothing was written from it."""

    status: Literal["ok", "unavailable", "not_read"]
    reason: str | None = Field(
        default=None,
        description="unavailable: the snapshot failure kind; not_read: "
        "user_inactive or no_firebase_uid",
    )
    retry_after_seconds: float | None = None
    read_at: datetime | None = Field(
        default=None, description="RevenueCat's request_date"
    )
    absent: bool | None = Field(
        default=None, description="RevenueCat holds no purchases for the uid"
    )
    entitlements: list[str] = Field(default_factory=list)
    standard_missing: bool = False
    standard: SupportLiveEntitlementResponse | None = None


class SupportUserAccessResponse(BaseModel):
    user: SupportUserResponse
    subscriptions: list[SupportSubscriptionRowResponse]
    decision: SupportAccessDecisionResponse
    flags: SupportAccessFlagsResponse
    live: SupportLiveReadResponse | None = Field(
        default=None, description="Only with ?live=true"
    )


class SupportSyncResultResponse(BaseModel):
    result: Literal["applied", "unchanged", "stale_skipped", "absent", "not_applied"]
    reason: str | None = Field(
        default=None,
        description="not_applied: why the read customer was not applied "
        "(owner_changed, conflicting_facts, absent_with_history, ...)",
    )
    rows_revoked: int
    row_created: bool
    markers_cleared: int
    standard_missing: bool


class SupportResyncResponse(BaseModel):
    user_id: str
    sync: SupportSyncResultResponse
    before: SupportAccessDecisionResponse
    after: SupportAccessDecisionResponse


class UnresolvedMarkerResponse(BaseModel):
    type: Literal["sync_required", "refund_pending", "transfer_source"]
    marked_at: datetime = Field(
        description="Oldest marker of this type (RevenueCat's event time)"
    )
    age_seconds: int


class UnresolvedQueueItemResponse(BaseModel):
    user_id: str
    firebase_uid: str | None
    oldest_marker_at: datetime
    oldest_marker_age_seconds: int
    markers: list[UnresolvedMarkerResponse]


class UnresolvedQueueResponse(BaseModel):
    items: list[UnresolvedQueueItemResponse]
    next_cursor: str | None = Field(
        description="Pass as ?cursor= for the next page; null on the last page"
    )
    limit: int
    checked_at: datetime = Field(description="The time ages are measured at")
