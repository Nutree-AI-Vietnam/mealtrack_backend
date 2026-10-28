"""Composition root for the subscription access support API."""

from src.domain.ports.subscription_access_support_repository_port import (
    SubscriptionAccessSupportRepositoryPort,
)
from src.infra.repositories.subscription_access_support_repository import (
    SubscriptionAccessSupportRepository,
)


def build_subscription_access_support_repository() -> (
    SubscriptionAccessSupportRepositoryPort
):
    """Read-only lookups, each in its own read-only unit of work."""
    return SubscriptionAccessSupportRepository()
