"""Request schemas for the subscription access support API (admin only)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SupportUserLookupRequest(BaseModel):
    """Body of POST /v1/admin/subscription-access/users/lookup.

    The query is usually a customer's email, so it travels in the body: a
    URL lands in the server's access log and in Sentry's request data.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=320,
        description="Email, Firebase uid or internal user id",
    )
    live: bool = Field(
        False,
        description="Also read RevenueCat (read-only). RevenueCat's GET "
        "/v1/subscribers creates an empty customer for a uid it has never "
        "seen, so only ask when needed.",
    )
