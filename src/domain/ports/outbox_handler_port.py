"""Domain port and result contracts for pluggable outbox event handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OutboxEventContext:
    """Execution context provided to an outbox event handler."""

    outbox_id: str
    event_id: str
    event_type: str
    retry_count: int
    created_at_iso: str


@dataclass(frozen=True)
class OutboxHandlerResult:
    """Outcome of an outbox handler execution."""

    success: bool
    error_message: str | None = None
    error_type: str | None = None
    status_code: int | None = None
    is_transient: bool = True
    is_paused: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, metadata: dict[str, Any] | None = None) -> OutboxHandlerResult:
        """Create a successful execution result."""
        return cls(success=True, metadata=metadata or {})

    @classmethod
    def transient_failure(
        cls,
        error_message: str,
        *,
        error_type: str | None = None,
        status_code: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OutboxHandlerResult:
        """Create a transient failure result eligible for retry."""
        return cls(
            success=False,
            error_message=error_message,
            error_type=error_type,
            status_code=status_code,
            is_transient=True,
            metadata=metadata or {},
        )

    @classmethod
    def permanent_failure(
        cls,
        error_message: str,
        *,
        error_type: str | None = None,
        status_code: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OutboxHandlerResult:
        """Create a permanent failure result that moves directly to DLQ."""
        return cls(
            success=False,
            error_message=error_message,
            error_type=error_type,
            status_code=status_code,
            is_transient=False,
            metadata=metadata or {},
        )

    @classmethod
    def paused(
        cls,
        error_message: str,
        *,
        error_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OutboxHandlerResult:
        """Keep an event pending without consuming its retry budget."""
        return cls(
            success=False,
            error_message=error_message,
            error_type=error_type,
            is_transient=True,
            is_paused=True,
            metadata=metadata or {},
        )


class OutboxEventHandler(Protocol):
    """Protocol implemented by domain event handlers registered in the outbox system."""

    async def handle(
        self,
        payload: dict[str, Any],
        context: OutboxEventContext,
    ) -> OutboxHandlerResult:
        """Handle the outbox event and return an execution result."""
        ...
