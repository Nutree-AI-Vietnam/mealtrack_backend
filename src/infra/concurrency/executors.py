"""Dedicated thread pools so blocking SDK work does not share the default executor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from src.infra.config.settings import get_settings

_firebase_executor: ThreadPoolExecutor | None = None


def get_firebase_executor() -> ThreadPoolExecutor:
    """Return the process-local Firebase Admin verification pool."""
    global _firebase_executor
    if _firebase_executor is None:
        workers = max(1, get_settings().FIREBASE_VERIFY_THREADS)
        _firebase_executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="firebase-verify",
        )
    return _firebase_executor


def shutdown_firebase_executor() -> None:
    """Stop the Firebase pool without blocking shutdown on in-flight verifies."""
    global _firebase_executor
    if _firebase_executor is None:
        return
    _firebase_executor.shutdown(wait=False, cancel_futures=True)
    _firebase_executor = None
