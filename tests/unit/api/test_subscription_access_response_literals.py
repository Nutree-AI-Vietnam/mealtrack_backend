"""The read models behind the support and monitoring responses carry the
same literal values as the response fields they fill.

The routes copy these fields into Literal-typed response models; with the
sources typed as the same Literals, mypy checks the copy (no type: ignore)
and a value the response would reject cannot be produced unnoticed.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Literal, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel

from src.api.schemas.response.admin_subscription_access_responses import (
    SupportAccessDecisionResponse,
    SupportLiveEntitlementResponse,
    SupportLiveReadResponse,
    SupportSyncResultResponse,
    UnresolvedMarkerResponse,
)
from src.api.schemas.response.subscription_access import (
    SubscriptionAccessHealthReasonResponse,
    SubscriptionAccessHealthResponse,
)
from src.app.services.subscription_access_health import (
    HealthReason,
    SubscriptionAccessHealthReport,
)
from src.app.services.subscription_access_support import AccessDecision, LiveRead
from src.domain.ports.subscription_access_refresh_port import (
    ProviderEntitlement,
    SubscriptionAccessRefresh,
)
from src.domain.ports.subscription_access_support_repository_port import (
    UnresolvedAccessUser,
)


def _literals(annotation: Any) -> set[Any]:
    """Every literal value in an annotation (unions and Optional flattened)."""
    if get_origin(annotation) is Literal:
        return set(get_args(annotation))
    if get_origin(annotation) in (typing.Union, types.UnionType):
        return set().union(*(_literals(arg) for arg in get_args(annotation)))
    return set()


def _field(model: type[BaseModel], name: str) -> set[Any]:
    return _literals(model.model_fields[name].annotation)


def _marker_types() -> set[Any]:
    returned = get_type_hints(UnresolvedAccessUser.markers)["return"]
    (pair,) = get_args(returned)  # list[tuple[type, datetime]]
    return _literals(get_args(pair)[0])


CASES = [
    (
        get_type_hints(AccessDecision)["outcome"],
        SupportAccessDecisionResponse,
        "outcome",
    ),
    (get_type_hints(AccessDecision)["status"], SupportAccessDecisionResponse, "status"),
    (get_type_hints(LiveRead)["status"], SupportLiveReadResponse, "status"),
    (
        get_type_hints(ProviderEntitlement)["status"],
        SupportLiveEntitlementResponse,
        "status",
    ),
    (
        get_type_hints(SubscriptionAccessRefresh)["result"],
        SupportSyncResultResponse,
        "result",
    ),
    (
        get_type_hints(SubscriptionAccessHealthReport)["status"],
        SubscriptionAccessHealthResponse,
        "status",
    ),
    (
        get_type_hints(HealthReason)["code"],
        SubscriptionAccessHealthReasonResponse,
        "code",
    ),
]


@pytest.mark.parametrize(
    ("source", "model", "name"),
    CASES,
    ids=[f"{case[1].__name__}.{case[2]}" for case in CASES],
)
def test_source_field_has_the_response_literals(source, model, name):
    assert _literals(source), f"{model.__name__}.{name}: its source is not a Literal"
    assert _literals(source) == _field(model, name)


def test_marker_types_are_the_queue_response_literals():
    assert _marker_types() == _field(UnresolvedMarkerResponse, "type")
