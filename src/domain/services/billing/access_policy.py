"""The entitlement access rule (plan §5), implemented once for every reader.

    has_access(u, key, now) =
         a live grant covers now
      OR a store subscription for key is not paused/refunded/revoked/absent
         AND (lifetime OR now < max(expires_at, grace_expires_at))
         AND (production OR sandbox allowed for u)        -- D3

``will_renew``, unsubscribe and billing-issue flags are recorded but never
remove access. Billing retry after the grace period ends has no access because
its dates have passed, not because of its state label.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from src.domain.model.billing.constants import (
    NO_ACCESS_STORE_STATES,
    BillingEnvironment,
    EntitlementState,
    PeriodType,
    PrimarySource,
    SandboxPolicy,
    Store,
    StoreSubscriptionState,
)
from src.domain.model.billing.ledger import EntitlementGrant, UserEntitlement

_GRACE_STORE_STATES = frozenset(
    {StoreSubscriptionState.IN_GRACE, StoreSubscriptionState.BILLING_RETRY}
)


class StoreSubscriptionView(Protocol):
    """What the rule reads from a subscription: a ledger row or a fresh snapshot row."""

    @property
    def subscription_key(self) -> str: ...
    @property
    def entitlement_key(self) -> str | None: ...
    @property
    def state(self) -> str: ...
    @property
    def environment(self) -> str: ...
    @property
    def store(self) -> str: ...
    @property
    def product_id(self) -> str: ...
    @property
    def period_type(self) -> str | None: ...
    @property
    def will_renew(self) -> bool | None: ...
    @property
    def is_lifetime(self) -> bool: ...
    @property
    def expires_at(self) -> datetime | None: ...
    @property
    def grace_expires_at(self) -> datetime | None: ...
    @property
    def billing_issue_detected_at(self) -> datetime | None: ...


def sandbox_access_allowed(
    policy: str | None,
    user_id: str,
    allowed_user_ids: Collection[str] = (),
) -> bool:
    """D3. ``all`` (the default, and the fallback for unknown values) keeps
    today's behaviour, where RevenueCat grants sandbox purchases."""
    normalized = (policy or SandboxPolicy.ALL).strip().lower()
    if normalized == SandboxPolicy.NONE:
        return False
    if normalized == SandboxPolicy.ALLOWLIST:
        return user_id in allowed_user_ids
    return True


def parse_user_id_allowlist(raw: str | None) -> frozenset[str]:
    """``ENTITLEMENT_SANDBOX_ALLOWED_USER_IDS``: comma-separated ``users.id``."""
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def store_access_end(sub: StoreSubscriptionView) -> datetime | None:
    """When a store subscription stops granting access; None for lifetime."""
    if sub.is_lifetime:
        return None
    expires_at = _utc(sub.expires_at)
    grace_expires_at = _utc(sub.grace_expires_at)
    if expires_at is None:
        return grace_expires_at
    if grace_expires_at is None:
        return expires_at
    return max(expires_at, grace_expires_at)


def store_subscription_grants_access(
    sub: StoreSubscriptionView, now: datetime, *, sandbox_allowed: bool
) -> bool:
    if sub.state in NO_ACCESS_STORE_STATES:
        return False
    if not _environment_allowed(sub, sandbox_allowed):
        return False
    if sub.is_lifetime:
        return True
    end = store_access_end(sub)
    return end is not None and _aware(now) < end


def compute_projection(
    user_id: str,
    entitlement_key: str,
    store_subs: Iterable[StoreSubscriptionView],
    grants: Iterable[EntitlementGrant],
    now: datetime,
    sandbox_allowed: bool,
    *,
    previous: UserEntitlement | None = None,
    provider_synced_at: datetime | None = None,
) -> UserEntitlement:
    """Recompute the projection row from the ledger. DB-only, never calls a provider.

    ``previous`` carries the version (bumped by one), the sticky
    ``ever_had_access`` flag and the sync/hint timestamps. ``provider_synced_at``
    is set when this recompute follows a provider fetch.
    """
    now = _aware(now)
    eligible = [
        sub
        for sub in store_subs
        if sub.entitlement_key == entitlement_key
        and _environment_allowed(sub, sandbox_allowed)
    ]
    live_subs = [sub for sub in eligible if sub.state not in NO_ACCESS_STORE_STATES]
    key_grants = [grant for grant in grants if grant.entitlement_key == entitlement_key]
    live_grants = [grant for grant in key_grants if not grant.is_revoked]

    granting_subs = [
        sub
        for sub in live_subs
        if store_subscription_grants_access(sub, now, sandbox_allowed=sandbox_allowed)
    ]
    granting_grants = [grant for grant in live_grants if _grant_live(grant, now)]
    has_access = bool(granting_subs or granting_grants)

    ever_had_access = (
        has_access
        or bool(eligible)
        or any(_grant_was_ever_live(grant, now) for grant in key_grants)
        or (previous is not None and previous.ever_had_access)
    )

    fields = _primary_fields(
        ever_had_access=ever_had_access,
        granting_subs=granting_subs,
        granting_grants=granting_grants,
        live_subs=live_subs,
        live_grants=live_grants,
        now=now,
    )
    access_until: datetime | None
    is_lifetime = False
    if has_access:
        access_until, is_lifetime = _coverage_end(live_subs, live_grants, now)
    else:
        access_until = fields.lapsed_until

    return UserEntitlement(
        user_id=user_id,
        entitlement_key=entitlement_key,
        has_access=has_access,
        state=fields.state,
        access_until=access_until,
        is_lifetime=is_lifetime,
        next_change_at=_next_change_at(live_subs, live_grants, now),
        will_renew=fields.will_renew,
        period_type=fields.period_type,
        primary_source=fields.primary_source,
        primary_ref=fields.primary_ref,
        product_id=fields.product_id,
        store=fields.store,
        is_sandbox=fields.is_sandbox,
        ever_had_access=ever_had_access,
        version=(previous.version + 1) if previous is not None else 1,
        computed_at=now,
        last_provider_sync_at=(
            provider_synced_at
            if provider_synced_at is not None
            else (previous.last_provider_sync_at if previous is not None else None)
        ),
        last_purchase_hint_at=(
            previous.last_purchase_hint_at if previous is not None else None
        ),
    )


def effective_has_access(projection: UserEntitlement, now: datetime) -> bool:
    """Read-time rule: the stored ``has_access`` is a cache as of ``computed_at``.

    Callers recompute first when ``needs_recompute`` is true; this check alone
    already turns an expired cached "yes" into "no".
    """
    if not projection.has_access:
        return False
    if projection.is_lifetime:
        return True
    access_until = _utc(projection.access_until)
    return access_until is not None and _aware(now) < access_until


def needs_recompute(projection: UserEntitlement, now: datetime) -> bool:
    next_change_at = _utc(projection.next_change_at)
    return next_change_at is not None and next_change_at <= _aware(now)


@dataclass(slots=True)
class _PrimaryFields:
    state: str
    primary_source: str = PrimarySource.STORE
    primary_ref: str | None = None
    product_id: str | None = None
    store: str | None = None
    is_sandbox: bool = False
    will_renew: bool | None = None
    period_type: str | None = None
    lapsed_until: datetime | None = None


def _primary_fields(
    *,
    ever_had_access: bool,
    granting_subs: Sequence[StoreSubscriptionView],
    granting_grants: Sequence[EntitlementGrant],
    live_subs: Sequence[StoreSubscriptionView],
    live_grants: Sequence[EntitlementGrant],
    now: datetime,
) -> _PrimaryFields:
    """Pick the source the API describes. A store subscription that grants access
    wins over a grant, so a paying user's renewal status stays visible."""
    if granting_subs:
        sub = max(granting_subs, key=lambda candidate: _store_rank(candidate, now))
        return _store_fields(sub, _store_state(sub, now), will_renew=sub.will_renew)
    if granting_grants:
        grant = max(granting_grants, key=lambda candidate: candidate.ends_at)
        return _grant_fields(grant, EntitlementState.ACTIVE)

    lapsed_state = (
        EntitlementState.EXPIRED if ever_had_access else EntitlementState.NONE
    )
    # No access: describe the most recently lapsed source, if any.
    lapsed_sub = _latest_lapsed_sub(live_subs, now)
    lapsed_grant = _latest_lapsed_grant(live_grants, now)
    grant_end = _aware(lapsed_grant.ends_at) if lapsed_grant is not None else None
    if lapsed_sub is not None and (grant_end is None or lapsed_sub[0] >= grant_end):
        sub_end, sub = lapsed_sub
        fields = _store_fields(sub, lapsed_state, will_renew=False)
        fields.lapsed_until = sub_end
        return fields
    if lapsed_grant is not None:
        fields = _grant_fields(lapsed_grant, lapsed_state)
        fields.lapsed_until = grant_end
        return fields
    return _PrimaryFields(state=lapsed_state)


def _store_fields(
    sub: StoreSubscriptionView, state: str, *, will_renew: bool | None
) -> _PrimaryFields:
    return _PrimaryFields(
        state=state,
        primary_source=PrimarySource.STORE,
        primary_ref=sub.subscription_key,
        product_id=sub.product_id,
        store=sub.store,
        is_sandbox=sub.environment == BillingEnvironment.SANDBOX,
        will_renew=will_renew,
        period_type=sub.period_type,
    )


def _grant_fields(grant: EntitlementGrant, state: str) -> _PrimaryFields:
    return _PrimaryFields(
        state=state,
        primary_source=PrimarySource.GRANT,
        primary_ref=grant.id,
        will_renew=False,
    )


def _store_state(sub: StoreSubscriptionView, now: datetime) -> str:
    """Projection state of an access-granting subscription at ``now``."""
    if sub.is_lifetime:
        return EntitlementState.ACTIVE
    expires_at = _utc(sub.expires_at)
    if (
        (expires_at is not None and now >= expires_at)
        or sub.billing_issue_detected_at is not None
        or sub.state in _GRACE_STORE_STATES
    ):
        return EntitlementState.GRACE
    if (
        sub.period_type == PeriodType.TRIAL
        or sub.state == StoreSubscriptionState.TRIALING
    ):
        return EntitlementState.TRIAL
    if sub.store == Store.PROMOTIONAL:
        return EntitlementState.ACTIVE
    if sub.will_renew is False:
        return EntitlementState.CANCELLED_ACTIVE
    return EntitlementState.ACTIVE


def _store_rank(
    sub: StoreSubscriptionView, now: datetime
) -> tuple[bool, bool, bool, datetime]:
    end = store_access_end(sub)
    return (
        sub.is_lifetime,
        sub.will_renew is True,
        _store_state(sub, now) != EntitlementState.GRACE,
        end if end is not None else now,
    )


def _latest_lapsed_sub(
    live_subs: Sequence[StoreSubscriptionView], now: datetime
) -> tuple[datetime, StoreSubscriptionView] | None:
    lapsed = [
        (end, sub)
        for sub in live_subs
        if (end := store_access_end(sub)) is not None and end <= now
    ]
    if not lapsed:
        return None
    return max(lapsed, key=lambda item: item[0])


def _latest_lapsed_grant(
    live_grants: Sequence[EntitlementGrant], now: datetime
) -> EntitlementGrant | None:
    lapsed = [grant for grant in live_grants if _aware(grant.ends_at) <= now]
    if not lapsed:
        return None
    return max(lapsed, key=lambda grant: grant.ends_at)


def _coverage_end(
    live_subs: Sequence[StoreSubscriptionView],
    live_grants: Sequence[EntitlementGrant],
    now: datetime,
) -> tuple[datetime | None, bool]:
    """End of the uninterrupted access that covers ``now``, chaining adjacent or
    overlapping sources (a grant that starts when a subscription ends extends it).
    Returns ``(None, True)`` when a lifetime source covers ``now``."""
    # (start, end): start None = open in the past; end None = never ends.
    intervals: list[tuple[datetime | None, datetime | None]] = []
    for sub in live_subs:
        if sub.is_lifetime:
            intervals.append((None, None))
        elif (end := store_access_end(sub)) is not None:
            intervals.append((None, end))
    for grant in live_grants:
        intervals.append((_aware(grant.starts_at), _aware(grant.ends_at)))

    current = now
    while True:
        extended = False
        for start, end in intervals:
            if start is not None and start > current:
                continue
            if end is None:
                return None, True
            if end > current:
                current = end
                extended = True
        if not extended:
            return current, False


def _next_change_at(
    live_subs: Sequence[StoreSubscriptionView],
    live_grants: Sequence[EntitlementGrant],
    now: datetime,
) -> datetime | None:
    """Earliest future instant at which access or state may change without a
    provider event: an expiry, a grace end, a grant start or a grant end."""
    boundaries: list[datetime] = []
    for sub in live_subs:
        if sub.is_lifetime:
            continue
        boundaries.extend(
            moment
            for moment in (_utc(sub.expires_at), _utc(sub.grace_expires_at))
            if moment is not None and moment > now
        )
    for grant in live_grants:
        boundaries.extend(
            moment
            for moment in (_aware(grant.starts_at), _aware(grant.ends_at))
            if moment > now
        )
    return min(boundaries) if boundaries else None


def _grant_live(grant: EntitlementGrant, now: datetime) -> bool:
    return not grant.is_revoked and _aware(grant.starts_at) <= now < _aware(
        grant.ends_at
    )


def _grant_was_ever_live(grant: EntitlementGrant, now: datetime) -> bool:
    starts_at = _aware(grant.starts_at)
    if starts_at > now:
        return False
    revoked_at = _utc(grant.revoked_at)
    return revoked_at is None or revoked_at > starts_at


def _environment_allowed(sub: StoreSubscriptionView, sandbox_allowed: bool) -> bool:
    return sub.environment == BillingEnvironment.PRODUCTION or (
        sandbox_allowed and sub.environment == BillingEnvironment.SANDBOX
    )


def _aware(value: datetime) -> datetime:
    """Treat naive datetimes (e.g. from in-memory fakes) as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else _aware(value)
