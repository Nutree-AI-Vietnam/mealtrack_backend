"""Process-local chat generation concurrency and provider circuit state."""

from __future__ import annotations

import asyncio

from src.infra.services.ai.provider_circuit_breaker import ProviderCircuitBreaker

_semaphore: asyncio.Semaphore | None = None
_circuit: ProviderCircuitBreaker | None = None


def get_chat_semaphore(limit: int) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, limit))
    return _semaphore


def get_chat_circuit_breaker() -> ProviderCircuitBreaker:
    global _circuit
    if _circuit is None:
        _circuit = ProviderCircuitBreaker(
            failure_threshold=5,
            failure_window_seconds=60,
            cooldown_seconds=30,
        )
    return _circuit


def reset_chat_concurrency_for_tests() -> None:
    global _semaphore, _circuit
    _semaphore = None
    _circuit = None
