"""
Adapter for RevenueCat subscription service.

This adapter implements the SubscriptionServicePort interface
and handles HTTP communication with RevenueCat API.
"""

import logging
import os
from datetime import datetime

import httpx

from src.domain.ports.subscription_service_port import SubscriptionServicePort

logger = logging.getLogger(__name__)


class RevenueCatAdapter(SubscriptionServicePort):
    """
    Adapter for RevenueCat subscription API.

    RevenueCat is the source of truth for subscription status.
    """

    BASE_URL = "https://api.revenuecat.com/v1"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("REVENUECAT_SECRET_API_KEY", "")

    async def get_subscriber_info(self, app_user_id: str) -> dict | None:
        """
        Get subscriber info from RevenueCat.

        Args:
            app_user_id: Your user ID (same as user.id)

        Returns:
            Subscriber data including entitlements and subscriptions
        """
        url = f"{self.BASE_URL}/subscribers/{app_user_id}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10.0)

                if response.status_code == 404:
                    logger.info(f"Subscriber not found in RevenueCat: {app_user_id}")
                    return None

                response.raise_for_status()
                return response.json()

        except httpx.HTTPError as e:
            logger.error(f"RevenueCat API error: {e}")
            return None

    async def has_active_subscription(self, app_user_id: str) -> bool:
        """
        Check if user has active subscription in RevenueCat.

        This is the source of truth for subscription status.
        """
        subscriber_info = await self.get_subscriber_info(app_user_id)

        if not subscriber_info:
            return False

        subscriber = subscriber_info.get("subscriber", {})
        entitlements = subscriber.get("entitlements", {})

        # Check if "standard" entitlement exists and is active
        if "standard" not in entitlements:
            return False

        standard = entitlements["standard"]
        expires_date_str = standard.get("expires_date")

        # NULL expires_date means lifetime access
        if expires_date_str is None:
            return True

        # Check if not expired
        try:
            expires_date = datetime.fromisoformat(
                expires_date_str.replace("Z", "+00:00")
            )
            return datetime.now(expires_date.tzinfo) < expires_date
        except Exception as e:
            logger.error(f"Error parsing expires_date: {e}")
            return False

    async def get_subscription_info(self, app_user_id: str) -> dict | None:
        """
        Get active subscription details.

        Returns product_id, expires_date, platform if user has active subscription.
        """
        subscriber_info = await self.get_subscriber_info(app_user_id)

        if not subscriber_info:
            return None

        subscriber = subscriber_info.get("subscriber", {})
        subscriptions = subscriber.get("subscriptions", {})

        # Find active subscription
        for product_id, sub_data in subscriptions.items():
            expires_date_str = sub_data.get("expires_date")

            if expires_date_str:
                try:
                    expires_date = datetime.fromisoformat(
                        expires_date_str.replace("Z", "+00:00")
                    )
                    if datetime.now(expires_date.tzinfo) < expires_date:
                        return {
                            "product_id": product_id,
                            "expires_date": expires_date,
                            "store": sub_data.get("store"),
                            "is_active": True,
                        }
                except Exception:
                    continue

        return None
