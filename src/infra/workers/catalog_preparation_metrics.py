"""Optional aggregate preparation metrics with fixed labels and no recipe IDs."""

from datetime import UTC, datetime

from src.observability import distribution_metric, increment_metric


def record_claim(claim):
    attributes = {"task": claim.task.value, "locale": claim.locale or "none"}
    try:
        increment_metric("catalog.preparation.claims", 1, attributes=attributes)
        if claim.created_at is not None:
            distribution_metric(
                "catalog.preparation.queue_age",
                max(0, (datetime.now(UTC) - claim.created_at).total_seconds()),
                unit="second",
                attributes=attributes,
            )
    except Exception:
        pass


def record_completion(claim, outcome, elapsed):
    attributes = {
        "task": claim.task.value,
        "locale": claim.locale or "none",
        "outcome": outcome,
    }
    try:
        increment_metric("catalog.preparation.outcomes", 1, attributes=attributes)
        distribution_metric(
            "catalog.preparation.duration",
            max(0, elapsed),
            unit="second",
            attributes=attributes,
        )
    except Exception:
        pass
