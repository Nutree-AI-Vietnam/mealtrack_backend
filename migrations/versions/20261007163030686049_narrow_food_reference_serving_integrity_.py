"""narrow food reference serving integrity generation bump

Revision ID: 20261007163030686049
Revises: 20261007093509175128
Create Date: 2026-10-07 23:30:30.688134

Every write to ``food_reference_serving_sizes`` bumped
``catalog_integrity_generation``, and that generation is part of every cached
food search key. Adopting a provider food inserts its serving rows, so each
adopting search flushed the whole search cache, including the page that same
search was about to cache.

The trigger still resets the reference to ``unknown`` and records an event for
every row it sees, but it now bumps the generation only when a cached page
could be showing the reference: not for references created by the same
transaction, not for quarantined references (search hides them), and only once
per reference per transaction.

Databases bootstrapped from model metadata never had this trigger; for them
the migration does nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261007163030686049"
down_revision: str | None = "20261007093509175128"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_SIGNATURE = "invalidate_food_reference_integrity_from_serving()"

# The trigger fires once per serving row. The first row of a write sees the
# reference as it was before this transaction; later rows see it already reset
# to "unknown", so the serving_changed event recorded by the first row (stamped
# with the transaction start time) is what marks the reference as handled.
_BUMP_WHEN_SHOWN_FUNCTION = """
CREATE OR REPLACE FUNCTION invalidate_food_reference_integrity_from_serving()
RETURNS trigger AS $$
DECLARE
    reference_id integer;
    previous_status text;
    reference_created_at timestamptz;
    active_policy text;
BEGIN
    reference_id := COALESCE(NEW.food_reference_id, OLD.food_reference_id);
    SELECT integrity_status, created_at
    INTO previous_status, reference_created_at
    FROM food_reference WHERE id = reference_id FOR UPDATE;
    SELECT active_policy_version INTO active_policy
    FROM food_reference_integrity_control WHERE id = 1 FOR UPDATE;
    UPDATE food_reference
    SET integrity_status = 'unknown',
        integrity_policy_version = NULL,
        integrity_checked_at = NULL,
        integrity_reason = 'serving_changed',
        integrity_input_digest = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = reference_id;
    -- Cached search pages never contain quarantined references or references
    -- created by this transaction.
    IF previous_status IS DISTINCT FROM 'quarantined'
        AND reference_created_at IS DISTINCT FROM CURRENT_TIMESTAMP
        AND NOT EXISTS (
            SELECT 1 FROM food_reference_integrity_events
            WHERE food_reference_id = reference_id
                AND reason_code = 'serving_changed'
                AND created_at = CURRENT_TIMESTAMP
        )
    THEN
        UPDATE food_reference_integrity_control
        SET catalog_integrity_generation = catalog_integrity_generation + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1;
    END IF;
    INSERT INTO food_reference_integrity_events (
        id, food_reference_id, before_status, after_status,
        reason_code, policy_version, actor_kind, created_at
    ) VALUES (
        md5(random()::text || clock_timestamp()::text), reference_id,
        COALESCE(previous_status, 'unknown'), 'unknown',
        'serving_changed', active_policy, 'system', CURRENT_TIMESTAMP
    );
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_ALWAYS_BUMP_FUNCTION = """
CREATE OR REPLACE FUNCTION invalidate_food_reference_integrity_from_serving()
RETURNS trigger AS $$
DECLARE
    reference_id integer;
    previous_status text;
    active_policy text;
BEGIN
    reference_id := COALESCE(NEW.food_reference_id, OLD.food_reference_id);
    SELECT integrity_status INTO previous_status
    FROM food_reference WHERE id = reference_id FOR UPDATE;
    SELECT active_policy_version INTO active_policy
    FROM food_reference_integrity_control WHERE id = 1 FOR UPDATE;
    UPDATE food_reference
    SET integrity_status = 'unknown',
        integrity_policy_version = NULL,
        integrity_checked_at = NULL,
        integrity_reason = 'serving_changed',
        integrity_input_digest = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = reference_id;
    UPDATE food_reference_integrity_control
    SET catalog_integrity_generation = catalog_integrity_generation + 1,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    INSERT INTO food_reference_integrity_events (
        id, food_reference_id, before_status, after_status,
        reason_code, policy_version, actor_kind, created_at
    ) VALUES (
        md5(random()::text || clock_timestamp()::text), reference_id,
        COALESCE(previous_status, 'unknown'), 'unknown',
        'serving_changed', active_policy, 'system', CURRENT_TIMESTAMP
    );
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _replace_trigger_function(definition: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    installed = bind.scalar(
        sa.text("SELECT to_regprocedure(:signature) IS NOT NULL"),
        {"signature": _FUNCTION_SIGNATURE},
    )
    if installed:
        op.execute(sa.text(definition))


def upgrade() -> None:
    _replace_trigger_function(_BUMP_WHEN_SHOWN_FUNCTION)


def downgrade() -> None:
    _replace_trigger_function(_ALWAYS_BUMP_FUNCTION)
