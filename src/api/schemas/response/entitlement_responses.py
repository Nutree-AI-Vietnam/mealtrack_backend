"""Response schemas for the entitlement endpoints (NM-680).

``EntitlementsResponse`` documents the schema-v1 contract shared with the
Flutter Freezed DTO. The route serialises ``EntitlementsView.to_contract()``
as is, so timestamps are ISO-8601 UTC strings with a ``Z`` suffix.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EntitlementStateLiteral = Literal[
    "trial", "active", "grace", "cancelled_active", "expired", "none", "unknown"
]


class EntitlementDetailResponse(BaseModel):
    active: bool
    state: str
    access_until: str | None = Field(
        default=None, description="ISO-8601 UTC; null for lifetime"
    )
    will_renew: bool | None = None
    period_type: str | None = None
    product_id: str | None = None
    store: str | None = None
    source: Literal["store", "grant"]
    is_sandbox: bool


class EntitlementsResponse(BaseModel):
    schema_version: int = 1
    authority: Literal["backend", "rc"]
    version: int
    server_time: str
    has_access: bool
    state: EntitlementStateLiteral
    ever_subscribed: bool
    entitlements: dict[str, EntitlementDetailResponse]
    synced_at: str | None = None
    stale: bool
    sync_error: str | None = None
    ttl_seconds: int


class EntitlementGrantResponse(BaseModel):
    id: str
    user_id: str
    entitlement_key: str
    source: str
    source_ref: str | None = None
    reason: str
    starts_at: datetime
    ends_at: datetime
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    revoked_by: str | None = None
    created_by: str
    created_at: datetime | None = None


class EntitlementProjectionResponse(BaseModel):
    """Raw ``user_entitlements`` row (admin views only)."""

    user_id: str
    entitlement_key: str
    has_access: bool
    effective_has_access: bool
    state: str
    access_until: datetime | None = None
    is_lifetime: bool
    next_change_at: datetime | None = None
    will_renew: bool | None = None
    period_type: str | None = None
    primary_source: str
    primary_ref: str | None = None
    product_id: str | None = None
    store: str | None = None
    is_sandbox: bool
    ever_had_access: bool
    version: int
    computed_at: datetime
    last_provider_sync_at: datetime | None = None
    last_purchase_hint_at: datetime | None = None


class EntitlementGrantMutationResponse(BaseModel):
    grant: EntitlementGrantResponse
    # False when an idempotent retry returned an existing grant or revoke.
    changed: bool
    projection: EntitlementProjectionResponse | None = None


class StoreSubscriptionResponse(BaseModel):
    id: str
    provider: str
    store: str
    environment: str
    subscription_key: str
    source_customer_id: str
    product_id: str
    entitlement_key: str | None = None
    state: str
    period_type: str | None = None
    ownership_type: str | None = None
    will_renew: bool | None = None
    is_lifetime: bool
    purchased_at: datetime | None = None
    expires_at: datetime | None = None
    grace_expires_at: datetime | None = None
    unsubscribe_detected_at: datetime | None = None
    billing_issue_detected_at: datetime | None = None
    refunded_at: datetime | None = None
    absent_since: datetime | None = None
    fetched_at: datetime
    last_synced_at: datetime
    version: int


class BillingProviderEventResponse(BaseModel):
    """Inbox row without its raw payload."""

    id: str
    provider: str
    provider_event_id: str
    event_type: str
    status: str
    environment: str | None = None
    app_user_id: str | None = None
    event_ts: datetime | None = None
    park_reason: str | None = None
    attempts: int
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    received_at: datetime
    processed_at: datetime | None = None


class EntitlementDebugResponse(BaseModel):
    user_id: str
    server_time: datetime
    # What GET /v1/me/entitlements would answer if the backend were authority.
    entitlements: dict[str, Any]
    projections: list[EntitlementProjectionResponse]
    store_subscriptions: list[StoreSubscriptionResponse]
    grants: list[EntitlementGrantResponse]
    events: list[BillingProviderEventResponse]
