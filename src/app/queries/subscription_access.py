"""Queries for the authenticated user's subscription access."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetMyEntitlementsQuery(Query):
    user_id: str
