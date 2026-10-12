"""Card and chart payloads in the shape the app parses.

Mirrors ``RecapStatCard`` and the ``RecapViz`` union (keyed by ``type``) in
the app's ``recap_models.dart``. Optional card fields are left out rather
than sent as null.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def card(
    kind: str,
    icon: str,
    title: str,
    *,
    count: int | None = None,
    prefix: str | None = None,
    suffix: str | None = None,
    value: str | None = None,
    unit: str = "",
    note: str = "",
    viz: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A stat card. ``value`` replaces the animated ``count`` when set."""
    payload: dict[str, Any] = {"kind": kind, "icon": icon, "title": title}
    optional = {"count": count, "prefix": prefix, "suffix": suffix, "value": value}
    payload.update({key: item for key, item in optional.items() if item is not None})
    payload["unit"] = unit
    payload["note"] = note
    if viz is not None:
        payload["viz"] = viz
    return payload


def spark(
    vals: Sequence[float],
    top: float,
    target: float,
    invert: bool = False,
    targets: Sequence[float] | None = None,
    floors: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Daily bars against a target line. ``invert`` marks lower as better.

    ``targets`` gives each day its own target, for weeks whose daily
    targets differ; the app colors each bar against its day's value.
    ``floors`` gives each day the least it should reach, for lower-is-better
    bars that can also fall short; the app mutes bars below it.
    """
    payload: dict[str, Any] = {
        "type": "spark",
        "vals": [_r2(value) for value in vals],
        "max": _r2(top),
        "target": _r2(target),
        "invert": invert,
    }
    if targets is not None:
        payload["targets"] = [_r2(value) for value in targets]
    if floors is not None:
        payload["floors"] = [_r2(value) for value in floors]
    return payload


def spark_max(
    vals: Sequence[float], target: float, headroom: float, target_headroom: float
) -> float:
    """Chart ceiling: room above the tallest bar and above the target line."""
    return max(max(vals, default=0.0) * headroom, target * target_headroom)


def dots(on: Sequence[int], color: str | None = None) -> dict[str, Any]:
    """Seven weekday dots with ``on`` (0 is Monday) filled in."""
    payload: dict[str, Any] = {"type": "dots", "on": list(on)}
    if color is not None:
        payload["color"] = color
    return payload


def bar(value: float, target: float) -> dict[str, Any]:
    """Progress bar of ``value`` toward ``target``."""
    return {"type": "bar", "v": _r2(value), "t": _r2(target)}


def _r2(value: float) -> float:
    return round(float(value), 2)
