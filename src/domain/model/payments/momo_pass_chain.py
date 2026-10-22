"""Settle a buyer's MoMo pass windows that RevenueCat may never have granted.

RevenueCat promotional grants start when they are made and end at the time
sent, so one grant delivers every window that ends by then. A window stays
unconfirmed until a grant covering it is known to have landed. It lapses once
its start is more than the duplicate tolerance in the past: resending it would
grant time that is already over.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

# RevenueCat treats a grant ending within 2 h of an active promotional's
# expiry as a duplicate of it.
DUPLICATE_TOLERANCE = timedelta(hours=2)


@dataclass(frozen=True)
class PassWindow:
    """The access window reserved for one paid, unconfirmed order."""

    order_id: str
    starts_at: datetime
    ends_at: datetime
    duration: timedelta

    def lapsed(self, now: datetime) -> bool:
        return self.starts_at < now - DUPLICATE_TOLERANCE


@dataclass(frozen=True)
class ChainRecovery:
    delivered: frozenset[str]
    """Orders whose window RevenueCat already covers."""
    moved: tuple[PassWindow, ...]
    """Windows stacked again, with their new bounds."""


def recover_chain(
    windows: list[PassWindow],
    confirmed_end: datetime | None,
    upstream_ends: list[datetime],
    now: datetime,
) -> ChainRecovery:
    """Settle a buyer's unconfirmed windows, given in chain order.

    A window is delivered when it ends by the end of a confirmed window, or by
    a promotional RevenueCat holds at one of these windows' ends (a grant whose
    answer was lost). Nothing is granted while the buyer holds a lapsed window,
    so the grant that covered a window started within the tolerance of it.
    When any other window has lapsed, all of them are stacked again, in order,
    from now or the latest end RevenueCat or a confirmed grant holds, so each
    paid week is granted once and the end sent only grows.
    """
    held = [
        end
        for end in upstream_ends
        if any(abs(end - window.ends_at) <= DUPLICATE_TOLERANCE for window in windows)
    ]
    covered = max(_known(confirmed_end, *held), default=None)
    delivered = frozenset(
        window.order_id
        for window in windows
        if covered and window.ends_at <= covered + DUPLICATE_TOLERANCE
    )
    remaining = [window for window in windows if window.order_id not in delivered]
    if not any(window.lapsed(now) for window in remaining):
        return ChainRecovery(delivered, ())
    start = max(_known(now, covered, *upstream_ends))
    moved = []
    for window in remaining:
        moved.append(replace(window, starts_at=start, ends_at=start + window.duration))
        start += window.duration
    return ChainRecovery(delivered, tuple(moved))


def _known(*moments: datetime | None) -> list[datetime]:
    return [moment for moment in moments if moment is not None]
