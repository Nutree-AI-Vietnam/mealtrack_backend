"""Queries for the authenticated user's subscription access."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetMyEntitlementsQuery(Query):
    user_id: str


@dataclass
class CheckSubscriptionAccessQuery(Query):
    """Whether the user may use a paid backend route right now."""

    user_id: str
