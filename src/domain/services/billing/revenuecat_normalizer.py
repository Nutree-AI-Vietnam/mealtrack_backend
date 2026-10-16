"""Normalize a RevenueCat REST v1 ``GET /subscribers/{app_user_id}`` body (D10).

Pure function, no I/O: the infra provider adapter fetches the JSON and passes it
here. Field semantics follow RevenueCat's own SDKs, which parse the same body:

- ``subscriber.entitlements[k].product_identifier`` names the product that
  currently backs entitlement ``k``. It is the only source of
  ``entitlement_key`` (D12: family-shared purchases count, as RC counts them).
- ``subscriptions`` is keyed by product id and holds the latest period.
- ``non_subscriptions`` lists one-off purchases per product, oldest first.
- ``will_renew`` is false for promotional, lifetime, unsubscribed or
  billing-issue subscriptions.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from src.domain.exceptions.billing_exceptions import InvalidProviderPayload
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    PROVIDER_REVENUECAT,
    BillingEnvironment,
    OwnershipType,
    PeriodType,
    Store,
    StoreSubscriptionState,
)
from src.domain.model.billing.snapshot import (
    CustomerSnapshot,
    NormalizedSubscription,
    ProviderEntitlement,
)

_STORES: dict[str, Store] = {
    "app_store": Store.APP_STORE,
    "mac_app_store": Store.APP_STORE,
    "play_store": Store.PLAY_STORE,
    "stripe": Store.STRIPE,
    "rc_billing": Store.RC_BILLING,
    "promotional": Store.PROMOTIONAL,
    "amazon": Store.AMAZON,
}
_PERIOD_TYPES = frozenset(PeriodType)
_OWNERSHIP_TYPES = frozenset(OwnershipType)


def revenuecat_subscription_key(original_app_user_id: str, product_id: str) -> str:
    """v1 key: the canonical customer plus the product (plan §5)."""
    return f"rc:{original_app_user_id}:{product_id}"


def normalize_revenuecat_subscriber(
    payload: Mapping[str, Any],
    *,
    fetched_at: datetime,
    requested_customer_id: str | None = None,
) -> CustomerSnapshot:
    """Turn a v1 subscriber body into a provider-neutral snapshot.

    ``fetched_at`` is when the fetch started; subscription states are evaluated
    at that instant. Raises ``InvalidProviderPayload`` rather than guessing, so a
    malformed body can never reduce anyone's access.
    """
    now = _as_utc(fetched_at)
    subscriber = payload.get("subscriber")
    if not isinstance(subscriber, Mapping):
        raise _invalid("missing subscriber object")
    canonical_id = subscriber.get("original_app_user_id")
    if not isinstance(canonical_id, str) or not canonical_id:
        raise _invalid("missing subscriber.original_app_user_id")

    entitlements = _parse_entitlements(subscriber.get("entitlements"))
    entitlement_by_product = _entitlement_by_product(entitlements)

    rows: dict[str, NormalizedSubscription] = {}
    for product_id, entry in _mapping(
        subscriber.get("subscriptions"), "subscriptions"
    ).items():
        if not isinstance(entry, Mapping):
            raise _invalid(f"subscriptions[{product_id!r}] is not an object")
        entitlement_key = entitlement_by_product.get(product_id)
        row = _subscription_row(
            canonical_id,
            product_id,
            entry,
            entitlement=entitlements.get(entitlement_key) if entitlement_key else None,
            entitlement_key=entitlement_key,
            now=now,
        )
        rows[row.subscription_key] = row

    non_subscriptions = _mapping(
        subscriber.get("non_subscriptions"), "non_subscriptions"
    )
    for product_id, purchases in non_subscriptions.items():
        entitlement_key = entitlement_by_product.get(product_id)
        if entitlement_key is None:
            continue  # consumables and other purchases that unlock nothing
        key = revenuecat_subscription_key(canonical_id, product_id)
        if key in rows:
            continue
        purchase_row = _non_subscription_row(
            canonical_id,
            product_id,
            purchases,
            entitlement=entitlements[entitlement_key],
            entitlement_key=entitlement_key,
            now=now,
        )
        if purchase_row is not None:
            rows[key] = purchase_row

    # Active entitlements no listed purchase backs (dashboard or temporary grants).
    listed_products = {row.product_id for row in rows.values()}
    for key_name in sorted(entitlements):
        entitlement = entitlements[key_name]
        promo_product = entitlement.product_id
        if not promo_product or promo_product in listed_products:
            continue
        if entitlement_by_product.get(promo_product) != key_name:
            continue
        if not _entitlement_grants_access(entitlement, now):
            continue
        promo_row = _promotional_row(
            canonical_id, promo_product, entitlement, key_name, now
        )
        rows[promo_row.subscription_key] = promo_row
        listed_products.add(promo_product)

    aliases: list[str] = []
    if requested_customer_id and requested_customer_id != canonical_id:
        aliases.append(requested_customer_id)

    return CustomerSnapshot(
        provider=PROVIDER_REVENUECAT,
        canonical_customer_id=canonical_id,
        aliases=aliases,
        subscriptions=list(rows.values()),
        fetched_at=now,
        requested_customer_id=requested_customer_id,
        entitlements=entitlements,
    )


def derive_subscription_state(
    *,
    now: datetime,
    expires_at: datetime | None,
    grace_expires_at: datetime | None,
    billing_issue_detected_at: datetime | None,
    refunded_at: datetime | None,
    purchased_at: datetime | None,
    period_type: str | None,
    auto_resume_at: datetime | None,
    is_lifetime: bool,
) -> str:
    """Store state at ``now``. Access itself is decided later from the dates."""
    # A refund older than the current period was for an earlier period.
    if refunded_at is not None and (
        purchased_at is None or refunded_at >= purchased_at
    ):
        return StoreSubscriptionState.REFUNDED
    if is_lifetime:
        return StoreSubscriptionState.ACTIVE
    period_over = expires_at is None or expires_at <= now
    # Play pause: the paid period runs out, then the subscription waits to resume.
    if auto_resume_at is not None and period_over:
        return StoreSubscriptionState.PAUSED
    if not period_over:
        # RC keeps the period open through the store grace period while it retries.
        if billing_issue_detected_at is not None:
            return StoreSubscriptionState.IN_GRACE
        if period_type == PeriodType.TRIAL:
            return StoreSubscriptionState.TRIALING
        return StoreSubscriptionState.ACTIVE
    if grace_expires_at is not None and grace_expires_at > now:
        return StoreSubscriptionState.IN_GRACE
    if billing_issue_detected_at is not None:
        return StoreSubscriptionState.BILLING_RETRY
    return StoreSubscriptionState.EXPIRED


def _subscription_row(
    canonical_id: str,
    product_id: str,
    entry: Mapping[str, Any],
    *,
    entitlement: ProviderEntitlement | None,
    entitlement_key: str | None,
    now: datetime,
) -> NormalizedSubscription:
    if "expires_date" not in entry:
        # Only an explicit null means lifetime; a missing field is never guessed.
        raise _invalid(f"subscriptions[{product_id!r}] has no expires_date")
    expires_at = _timestamp(entry, "expires_date")
    grace_expires_at = _timestamp(entry, "grace_period_expires_date")
    if grace_expires_at is None and entitlement is not None:
        grace_expires_at = entitlement.grace_expires_at
    purchased_at = _timestamp(entry, "purchase_date")
    unsubscribe_detected_at = _timestamp(entry, "unsubscribe_detected_at")
    billing_issue_detected_at = _timestamp(entry, "billing_issues_detected_at")
    refunded_at = _timestamp(entry, "refunded_at")
    store = _store(entry.get("store"))
    period_type = _period_type(entry.get("period_type"))
    # RC lists promotional lifetime grants as subscriptions with no expiry.
    is_lifetime = expires_at is None
    state = derive_subscription_state(
        now=now,
        expires_at=expires_at,
        grace_expires_at=grace_expires_at,
        billing_issue_detected_at=billing_issue_detected_at,
        refunded_at=refunded_at,
        purchased_at=purchased_at,
        period_type=period_type,
        auto_resume_at=_timestamp(entry, "auto_resume_date"),
        is_lifetime=is_lifetime,
    )
    will_renew = not (
        store == Store.PROMOTIONAL
        or is_lifetime
        or unsubscribe_detected_at is not None
        or billing_issue_detected_at is not None
        or state in (StoreSubscriptionState.REFUNDED, StoreSubscriptionState.EXPIRED)
    )
    return NormalizedSubscription(
        subscription_key=revenuecat_subscription_key(canonical_id, product_id),
        store=store,
        environment=_environment(entry.get("is_sandbox")),
        source_customer_id=canonical_id,
        product_id=product_id,
        entitlement_key=entitlement_key,
        state=state,
        period_type=period_type,
        will_renew=will_renew,
        is_lifetime=is_lifetime,
        purchased_at=purchased_at,
        expires_at=expires_at,
        grace_expires_at=grace_expires_at,
        unsubscribe_detected_at=unsubscribe_detected_at,
        billing_issue_detected_at=billing_issue_detected_at,
        refunded_at=refunded_at,
        store_identifier=_optional_str(entry.get("store_transaction_id")),
        original_transaction_id=_optional_str(entry.get("original_transaction_id")),
        ownership_type=_ownership_type(entry.get("ownership_type")),
    )


def _non_subscription_row(
    canonical_id: str,
    product_id: str,
    purchases: Any,
    *,
    entitlement: ProviderEntitlement,
    entitlement_key: str,
    now: datetime,
) -> NormalizedSubscription | None:
    if not isinstance(purchases, list):
        raise _invalid(f"non_subscriptions[{product_id!r}] is not a list")
    if not purchases:
        return None
    for purchase in purchases:
        if not isinstance(purchase, Mapping):
            raise _invalid(f"non_subscriptions[{product_id!r}] has a non-object entry")
    latest = _latest_purchase(purchases)
    purchased_at = _timestamp(latest, "purchase_date") or entitlement.purchased_at
    refunded_at = _timestamp(latest, "refunded_at")
    # The entitlement carries the duration: null = lifetime, else non-renewing.
    expires_at = entitlement.expires_at
    is_lifetime = expires_at is None
    state = derive_subscription_state(
        now=now,
        expires_at=expires_at,
        grace_expires_at=entitlement.grace_expires_at,
        billing_issue_detected_at=None,
        refunded_at=refunded_at,
        purchased_at=purchased_at,
        period_type=PeriodType.NORMAL,
        auto_resume_at=None,
        is_lifetime=is_lifetime,
    )
    return NormalizedSubscription(
        subscription_key=revenuecat_subscription_key(canonical_id, product_id),
        store=_store(latest.get("store")),
        environment=_environment(latest.get("is_sandbox")),
        source_customer_id=canonical_id,
        product_id=product_id,
        entitlement_key=entitlement_key,
        state=state,
        period_type=PeriodType.NORMAL,
        will_renew=False,
        is_lifetime=is_lifetime,
        purchased_at=purchased_at,
        expires_at=expires_at,
        grace_expires_at=entitlement.grace_expires_at,
        unsubscribe_detected_at=None,
        billing_issue_detected_at=None,
        refunded_at=refunded_at,
        store_identifier=_optional_str(latest.get("store_transaction_id")),
    )


def _promotional_row(
    canonical_id: str,
    product_id: str,
    entitlement: ProviderEntitlement,
    entitlement_key: str,
    now: datetime,
) -> NormalizedSubscription:
    """An active entitlement no listed purchase backs (dashboard or temporary grant)."""
    is_lifetime = entitlement.expires_at is None
    state = derive_subscription_state(
        now=now,
        expires_at=entitlement.expires_at,
        grace_expires_at=entitlement.grace_expires_at,
        billing_issue_detected_at=None,
        refunded_at=None,
        purchased_at=entitlement.purchased_at,
        period_type=PeriodType.PROMOTIONAL,
        auto_resume_at=None,
        is_lifetime=is_lifetime,
    )
    return NormalizedSubscription(
        subscription_key=revenuecat_subscription_key(canonical_id, product_id),
        store=Store.PROMOTIONAL,
        environment=BillingEnvironment.PRODUCTION,
        source_customer_id=canonical_id,
        product_id=product_id,
        entitlement_key=entitlement_key,
        state=state,
        period_type=PeriodType.PROMOTIONAL,
        will_renew=False,
        is_lifetime=is_lifetime,
        purchased_at=entitlement.purchased_at,
        expires_at=entitlement.expires_at,
        grace_expires_at=entitlement.grace_expires_at,
        unsubscribe_detected_at=None,
        billing_issue_detected_at=None,
        refunded_at=None,
    )


def _parse_entitlements(raw: Any) -> dict[str, ProviderEntitlement]:
    entitlements: dict[str, ProviderEntitlement] = {}
    for key, entry in _mapping(raw, "entitlements").items():
        if not isinstance(entry, Mapping):
            raise _invalid(f"entitlements[{key!r}] is not an object")
        entitlements[key] = ProviderEntitlement(
            entitlement_key=key,
            product_id=_optional_str(entry.get("product_identifier")),
            purchased_at=_timestamp(entry, "purchase_date"),
            expires_at=_timestamp(entry, "expires_date"),
            grace_expires_at=_timestamp(entry, "grace_period_expires_date"),
        )
    return entitlements


def _entitlement_by_product(
    entitlements: Mapping[str, ProviderEntitlement],
) -> dict[str, str]:
    """product id -> entitlement key. One product may back several entitlements;
    the row keeps ``standard`` when it is one of them, else the first key."""
    by_product: dict[str, str] = {}
    for key in sorted(entitlements):
        product_id = entitlements[key].product_id
        if not product_id:
            continue
        if product_id not in by_product or key == ENTITLEMENT_STANDARD:
            by_product[product_id] = key
    return by_product


def _entitlement_grants_access(entitlement: ProviderEntitlement, now: datetime) -> bool:
    if entitlement.expires_at is None:
        return True
    end = max(
        entitlement.expires_at, entitlement.grace_expires_at or entitlement.expires_at
    )
    return now < end


def _latest_purchase(purchases: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """RC lists purchases oldest first; prefer the newest dated one."""
    latest = purchases[-1]
    latest_at = _timestamp(latest, "purchase_date")
    for purchase in purchases:
        when = _timestamp(purchase, "purchase_date")
        if when is not None and (latest_at is None or when > latest_at):
            latest, latest_at = purchase, when
    return latest


def _mapping(raw: Any, name: str) -> Mapping[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _invalid(f"subscriber.{name} is not an object")
    return raw


def _timestamp(entry: Mapping[str, Any], name: str) -> datetime | None:
    value = entry.get(name)
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise _invalid(f"{name} is not an ISO-8601 timestamp") from exc
        return _as_utc(parsed)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    raise _invalid(f"{name} has an unexpected type")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _store(raw: Any) -> str:
    if isinstance(raw, str):
        return _STORES.get(raw.strip().lower(), Store.OTHER)
    return Store.OTHER


def _environment(is_sandbox: Any) -> str:
    return (
        BillingEnvironment.SANDBOX
        if is_sandbox is True
        else BillingEnvironment.PRODUCTION
    )


def _period_type(raw: Any) -> str | None:
    if isinstance(raw, str) and raw.strip().lower() in _PERIOD_TYPES:
        return PeriodType(raw.strip().lower())
    return None


def _ownership_type(raw: Any) -> str | None:
    if isinstance(raw, str) and raw.strip().lower() in _OWNERSHIP_TYPES:
        return OwnershipType(raw.strip().lower())
    return None


def _optional_str(raw: Any) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def _invalid(detail: str) -> InvalidProviderPayload:
    return InvalidProviderPayload(
        f"RevenueCat v1 subscriber: {detail}", provider=PROVIDER_REVENUECAT
    )
