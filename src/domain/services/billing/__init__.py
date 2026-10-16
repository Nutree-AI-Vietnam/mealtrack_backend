"""Billing domain services: provider normalization and the access rule (NM-680)."""

from src.domain.services.billing.access_policy import (
    compute_projection,
    effective_has_access,
    needs_recompute,
    parse_user_id_allowlist,
    sandbox_access_allowed,
    store_access_end,
    store_subscription_grants_access,
)
from src.domain.services.billing.revenuecat_normalizer import (
    derive_subscription_state,
    normalize_revenuecat_subscriber,
    revenuecat_subscription_key,
)

__all__ = [
    "compute_projection",
    "derive_subscription_state",
    "effective_has_access",
    "needs_recompute",
    "normalize_revenuecat_subscriber",
    "parse_user_id_allowlist",
    "revenuecat_subscription_key",
    "sandbox_access_allowed",
    "store_access_end",
    "store_subscription_grants_access",
]
