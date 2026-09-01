"""Chat-specific domain errors mapped at the API boundary."""

from __future__ import annotations


class ChatBusyError(Exception):
    """Another assistant generation is already active for this user."""

    def __init__(self, retry_after_seconds: int = 5) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("A chat turn is already in progress")


class ChatIdempotencyConflictError(Exception):
    """The same Idempotency-Key was reused with a different request body."""

    def __init__(self) -> None:
        super().__init__("Idempotency key was reused with a different payload")


class ChatRateLimitedError(Exception):
    """Per-user chat turn budget or short-window limit was exceeded."""

    def __init__(self, retry_after_seconds: int, *, daily: bool = False) -> None:
        self.retry_after_seconds = retry_after_seconds
        self.daily = daily
        super().__init__("Chat turn limit exceeded")


class ChatProviderUnavailableError(Exception):
    """The completion provider is unavailable or the circuit is open."""

    def __init__(
        self, retry_after_seconds: int = 15, *, retryable: bool = True
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        self.retryable = retryable
        super().__init__("Chat provider is temporarily unavailable")
