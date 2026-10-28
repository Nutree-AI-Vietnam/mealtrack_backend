"""Queries for subscription access: the user's own, support's and monitoring's."""

from dataclasses import dataclass

from src.app.events.base import Query
from src.domain.ports.subscription_access_support_repository_port import (
    UnresolvedQueueCursor,
)


@dataclass
class GetMyEntitlementsQuery(Query):
    user_id: str


@dataclass
class CheckSubscriptionAccessQuery(Query):
    """Whether the user may use a paid backend route right now."""

    user_id: str


@dataclass
class GetSupportSubscriptionAccessQuery(Query):
    """Support: one user's rows and access decision, found by email, Firebase
    uid or internal id. ``live`` also reads RevenueCat, writing nothing."""

    query: str
    live: bool = False


@dataclass
class ListUnresolvedSubscriptionAccessQuery(Query):
    """Support's repair queue: live users with a marked RevenueCat row,
    oldest marker first, ``limit`` per page after ``after``."""

    limit: int
    after: UnresolvedQueueCursor | None = None
