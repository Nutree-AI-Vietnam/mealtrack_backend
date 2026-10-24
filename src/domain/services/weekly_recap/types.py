"""Building blocks of the recap engine.

An insight is a win card, an issue is a "room to improve" card that comes
with advice, and a habit is advice that has no card. Conditions and
renderers all take a :class:`RecapContext`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from src.domain.services.weekly_recap.config import RecapConfig
    from src.domain.services.weekly_recap.context import RecapContext

Card = dict[str, Any]


@dataclass(frozen=True)
class Move:
    """One plan item, plus the short phrase the share card reuses."""

    kind: str
    icon: str
    title: str
    impact: str
    why: str
    short: str

    def to_payload(self) -> dict[str, str]:
        """The advice row as the app parses it (``RecapAdvice``)."""
        return {
            "kind": self.kind,
            "icon": self.icon,
            "title": self.title,
            "why": self.why,
            "impact": self.impact,
        }


@dataclass(frozen=True)
class Insight:
    """A win card. ``stat`` gives its share-card tile, if it has one (it may
    also return None for a week whose card has no tile)."""

    id: str
    pri: int
    cond: Callable[[RecapContext], bool]
    card: Callable[[RecapContext], Card]
    stat: Callable[[RecapContext], list[str] | None] | None = None


@dataclass(frozen=True)
class Issue:
    """Something that held the week back, with the advice that addresses it."""

    id: str
    pri: int
    cond: Callable[[RecapContext], bool]
    card: Callable[[RecapContext], Card]
    advice: Callable[[RecapContext], Move]


@dataclass(frozen=True)
class Habit:
    """Advice without a card: reminders, streak goals, challenges."""

    id: str
    cond: Callable[[RecapContext], bool]
    advice: Callable[[RecapContext], Move]


class _Ranked(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def pri(self) -> int: ...


def ranked[RankedT: _Ranked](
    items: Sequence[RankedT], config: RecapConfig
) -> list[RankedT]:
    """Highest priority first; equal priorities keep their list order."""
    return sorted(items, key=lambda item: -config.pri(item.id, item.pri))
