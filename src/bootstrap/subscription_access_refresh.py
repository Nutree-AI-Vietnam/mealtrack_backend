"""Composition root for the subscription access refresh (self-heal) path."""

from collections.abc import Callable

from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessRefreshPort,
)
from src.domain.ports.subscription_service_port import SubscriptionServicePort
from src.infra.services.subscription_access_refresh_service import (
    RevenueCatAccessRefreshService,
)


def build_subscription_access_refresher(
    subscription_service_factory: Callable[[], SubscriptionServicePort],
) -> SubscriptionAccessRefreshPort:
    return RevenueCatAccessRefreshService(subscription_service_factory)
