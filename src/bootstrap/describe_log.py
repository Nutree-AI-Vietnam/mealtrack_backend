"""Composition root for Ghi nhanh: Idempotency-Key exact replay.

The describe routes reuse the claim-before-create store the manual meal route
uses, scoped per user and action. API modules reach it through here because
they may not import infra directly.
"""

from src.infra.services.durable_write_service import (
    DurableWriteConflictError,
    DurableWriteInProgressError,
    abandon_durable_write,
    begin_durable_write,
    canonicalize_fingerprint,
    complete_durable_write,
    normalize_idempotency_key,
)

HYDRATION_DESCRIBE_ACTION = "hydration_describe"
MOVEMENT_DESCRIBE_ACTION = "movement_describe"

__all__ = [
    "HYDRATION_DESCRIBE_ACTION",
    "MOVEMENT_DESCRIBE_ACTION",
    "DurableWriteConflictError",
    "DurableWriteInProgressError",
    "abandon_durable_write",
    "begin_durable_write",
    "canonicalize_fingerprint",
    "complete_durable_write",
    "normalize_idempotency_key",
]
