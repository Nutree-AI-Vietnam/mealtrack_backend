"""Which store products are streak restore packs, and how many restores each holds.

One rule, shared with the app: drop any ``:suffix`` (a Play base plan), then
the id must end in ``streak_restore_<n>`` with n of at least 1.
"""

from __future__ import annotations

import re

_PACK_ID = re.compile(r"streak_restore_([0-9]+)$")
_MARKER = "streak_restore"


def restore_count_for(product_id: str | None) -> int | None:
    """Restores in the pack, or None when the product is not a countable pack."""
    if not product_id:
        return None
    match = _PACK_ID.search(product_id.split(":", 1)[0])
    if match is None:
        return None
    count = int(match.group(1))
    return count if count >= 1 else None


def mentions_streak_restore(product_id: str | None) -> bool:
    """Whether a product id looks like a pack at all, countable or not.

    Store events for such products must never reach subscription handling.
    """
    return _MARKER in (product_id or "").lower()
