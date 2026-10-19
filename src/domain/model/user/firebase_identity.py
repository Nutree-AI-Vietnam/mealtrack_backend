"""Firebase UID occupancy for the users.firebase_uid unique index.

The column is NOT NULL and unique for every row, including inactive ones.
Soft-delete and re-registration must replace a live UID with a tombstone or
the next insert collides with ix_users_firebase_uid.
"""

from uuid import UUID

TOMBSTONE_PREFIX = "deleted:"


def tombstone_firebase_uid(user_id: UUID | str) -> str:
    """Return a unique, non-live firebase_uid that fits VARCHAR(128)."""
    tombstone = f"{TOMBSTONE_PREFIX}{user_id}"
    if len(tombstone) > 128:
        raise ValueError("tombstoned firebase_uid exceeds column length")
    return tombstone
