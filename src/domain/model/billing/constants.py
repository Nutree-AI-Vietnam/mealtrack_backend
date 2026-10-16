"""Constants for the provider-neutral entitlement ledger (NM-680).

String enums are ``StrEnum`` so their members compare equal to the plain strings
stored in the database and returned by providers.
"""

from enum import StrEnum

ENTITLEMENT_STANDARD = "standard"

PROVIDER_REVENUECAT = "revenuecat"
DEFAULT_PROJECT = "default"

# Manual grants are capped (D5): no lifetime grants in NM-680.
GRANT_MAX_DAYS = 366

# Inbox rows go to ``dead`` after this many failed attempts.
INBOX_MAX_ATTEMPTS = 10

# Stored webhook bodies (``billing_provider_events.raw``) are nulled after this
# many days (D9); account deletion nulls them at once.
RAW_PAYLOAD_RETENTION_DAYS = 90

# Backend kill switches (DB ``feature_flags`` rows; a missing row means OFF).
FLAG_ENT_LEDGER_WRITE = "ent_ledger_write"
FLAG_ENT_RECONCILE = "ent_reconcile"
FLAG_ENT_READ_API = "ent_read_api"
FLAG_ENT_READ_API_DEFER = "ent_read_api_defer"
# Flags with this prefix are server-only and never exposed to clients.
ENTITLEMENT_FLAG_PREFIX = "ent_"


class BillingEnvironment(StrEnum):
    PRODUCTION = "production"
    SANDBOX = "sandbox"


class Store(StrEnum):
    APP_STORE = "app_store"
    PLAY_STORE = "play_store"
    STRIPE = "stripe"
    RC_BILLING = "rc_billing"
    PROMOTIONAL = "promotional"
    AMAZON = "amazon"
    OTHER = "other"


class StoreSubscriptionState(StrEnum):
    """State of one store subscription as reported by the provider snapshot."""

    TRIALING = "trialing"
    ACTIVE = "active"
    IN_GRACE = "in_grace"
    BILLING_RETRY = "billing_retry"
    PAUSED = "paused"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    REVOKED = "revoked"
    # The customer's latest snapshot no longer lists the subscription (transfer).
    ABSENT = "absent"


# Store states that never grant access, whatever the dates say.
NO_ACCESS_STORE_STATES: frozenset[str] = frozenset(
    {
        StoreSubscriptionState.PAUSED,
        StoreSubscriptionState.REFUNDED,
        StoreSubscriptionState.REVOKED,
        StoreSubscriptionState.ABSENT,
    }
)


class EntitlementState(StrEnum):
    """Projection state served by ``GET /v1/me/entitlements``."""

    TRIAL = "trial"
    ACTIVE = "active"
    GRACE = "grace"
    CANCELLED_ACTIVE = "cancelled_active"
    EXPIRED = "expired"
    NONE = "none"
    # API only: no projection row yet, or RevenueCat is the authority.
    UNKNOWN = "unknown"


class PeriodType(StrEnum):
    TRIAL = "trial"
    INTRO = "intro"
    NORMAL = "normal"
    PROMOTIONAL = "promotional"
    PREPAID = "prepaid"


class OwnershipType(StrEnum):
    PURCHASED = "purchased"
    FAMILY_SHARED = "family_shared"


class PrimarySource(StrEnum):
    STORE = "store"
    GRANT = "grant"


class GrantSource(StrEnum):
    ADMIN = "admin"
    SUPPORT = "support"
    QA = "qa"
    MIGRATION = "migration"


class IdentityKind(StrEnum):
    PRIMARY = "primary"
    ALIAS = "alias"
    ANONYMOUS = "anonymous"
    LEGACY_LEAD = "legacy_lead"


class ProviderEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    UNMATCHED = "unmatched"
    RETRY = "retry"
    IGNORED = "ignored"
    DEAD = "dead"


class ParkReason(StrEnum):
    USER_NOT_FOUND = "user_not_found"
    DELETED_USER = "deleted_user"


class SandboxPolicy(StrEnum):
    """``ENTITLEMENT_SANDBOX_POLICY`` (D3)."""

    ALL = "all"
    ALLOWLIST = "allowlist"
    NONE = "none"


class SyncLane(StrEnum):
    """RevenueCat budget lanes, highest priority first."""

    PURCHASE_SYNC = "purchase_sync"
    WEBHOOK = "webhook"
    LOGIN_SYNC = "login_sync"
    RECONCILE = "reconcile"
    BACKFILL = "backfill"
