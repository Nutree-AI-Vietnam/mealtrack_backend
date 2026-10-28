"""Stable Sentry alert keys for subscription access failures.

Each page-worthy failure is captured with a fixed fingerprint whose first
part is one of the keys below. The Sentry connector also sends that key as
the event's ``alert`` tag, so an issue alert rule can match the tag whatever
the exception message or stack says. The rules the team creates for these
keys are in docs/runbooks/subscription-access-reconciliation.md
("Monitoring"); renaming a key breaks those rules.
"""

from __future__ import annotations

# RevenueCat state could not be verified for a contract or configuration
# reason (failure_kind malformed, http_4xx, api_key_missing,
# unsupported_store, unexpected), by a webhook, a refresh, a web finalize or
# a support call. Fingerprint: [key, failure_kind]. Transient kinds
# (transport, http_429, http_5xx) are never sent: their counter alerts.
ALERT_SNAPSHOT_FAILURE = "subscription_access.snapshot_failure"
# Applying a RevenueCat snapshot that was read raised an unexpected error (a
# bug or a database failure) in a refresh or a support resync, which then
# failed. Fingerprint: [key, source]. A webhook keeps its event's effects
# instead and reports the same failure as a snapshot failure 'unexpected'.
ALERT_SYNC_APPLY_ERROR = "subscription_access.sync_apply_error"
# The RevenueCat webhook raised and answered 500: RevenueCat retries a few
# times, then drops the event. Fingerprint: [key, exception type].
ALERT_WEBHOOK_PROCESSING_ERROR = "subscription_access.webhook_processing_error"
# A RevenueCat customer has entitlements but none named 'standard'
# (entitlement configuration drift). Fingerprint: [key].
ALERT_STANDARD_MISSING = "subscription_access.standard_missing"
# A sync removed access on an event that should not remove it.
# Fingerprint: [key]. Alerted on its rate, not on each event.
ALERT_UNEXPECTED_DOWNGRADE = "subscription_access.unexpected_downgrade"


def alert_fingerprint(alert: str, *parts: object) -> list[str]:
    """The fixed fingerprint for one alert key and its bounded parts."""
    return [alert, *("none" if part is None else str(part) for part in parts)]
