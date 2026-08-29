"""Guardrails for async-boundary hygiene: route-level commits, event-loop drivers,
unmanaged create_task, and raw session commits outside the UoW.

Allowlists represent KNOWN existing offenders tracked for future cleanup.
The allowlist must NOT grow — if a new file shows up here, fix it instead.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
SRC_ROOT = PROJECT_ROOT / "src"


# ---------------------------------------------------------------------------
# Allowlists — shrink as phases complete; never add new entries
# ---------------------------------------------------------------------------

# Route modules that still call `await db.commit()` / `await session.commit()`
# directly instead of delegating to a handler or application service.
ALLOWED_ROUTE_DB_COMMIT_FILES = set()

# Runtime modules (under src/) that drive the asyncio event loop directly via
# get_event_loop / set_event_loop / run_until_complete.
ALLOWED_SYNC_LOOP_DRIVER_FILES = set()

# Route/event-bus modules that use raw asyncio.create_task without a managed
# background-task runner.
ALLOWED_UNMANAGED_CREATE_TASK_FILES: set[str] = set()

# Application/API modules that call uow.session.commit() directly instead of
# using the UoW context manager or await uow.commit().
ALLOWED_UOW_SESSION_COMMIT_FILES = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(pat in text for pat in patterns)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_route_modules_do_not_call_db_commit_directly() -> None:
    """Route handlers must not commit the DB session directly.

    Committing inside a route bypasses the UoW / CQRS boundary and makes
    rollback behaviour unpredictable.  Move the commit into an application
    service or command handler.
    """
    routes_root = SRC_ROOT / "api" / "routes"
    commit_patterns = ["await db.commit()", "await session.commit()"]

    offenders = {
        _relative(path)
        for path in _python_files(routes_root)
        if _contains_any(path.read_text(encoding="utf-8"), commit_patterns)
    }

    assert offenders == ALLOWED_ROUTE_DB_COMMIT_FILES, (
        f"Route-level db.commit() bypasses UoW/CQRS; move to handler or "
        f"application service.\n"
        f"  Unexpected offenders : {offenders - ALLOWED_ROUTE_DB_COMMIT_FILES}\n"
        f"  Resolved (remove from allowlist): {ALLOWED_ROUTE_DB_COMMIT_FILES - offenders}"
    )


def test_runtime_source_does_not_drive_event_loop_directly() -> None:
    """Runtime code must not manually drive the asyncio event loop.

    Calls to get_event_loop / set_event_loop / run_until_complete in
    production code create hard-to-debug nesting bugs.  Use `async def` or a
    single top-level `asyncio.run()` call instead.
    """
    loop_driver_patterns = [
        "run_until_complete",
        "asyncio.get_event_loop",
        "asyncio.set_event_loop",
        "get_event_loop()",
        "set_event_loop(",
    ]

    offenders = {
        _relative(path)
        for path in _python_files(SRC_ROOT)
        if _contains_any(path.read_text(encoding="utf-8"), loop_driver_patterns)
    }

    assert offenders == ALLOWED_SYNC_LOOP_DRIVER_FILES, (
        "Runtime code must not drive the event loop; use async def or explicit "
        "asyncio.run() at the top level only.\n"
        f"  Unexpected offenders : {offenders - ALLOWED_SYNC_LOOP_DRIVER_FILES}\n"
        f"  Resolved (remove from allowlist): {ALLOWED_SYNC_LOOP_DRIVER_FILES - offenders}"
    )


def test_unmanaged_create_task_does_not_expand() -> None:
    """Raw asyncio.create_task in routes or event-bus code must not grow.

    Unmanaged tasks have no error propagation, no cancellation on shutdown, and
    no structured lifetime.  Replace with a supervised background-task runner.
    """
    scan_roots = [
        SRC_ROOT / "api" / "routes",
        SRC_ROOT / "infra" / "event_bus",
    ]

    offenders: set[str] = set()
    for root in scan_roots:
        for path in _python_files(root):
            rel = _relative(path)
            if "asyncio.create_task(" in path.read_text(encoding="utf-8"):
                offenders.add(rel)

    assert offenders == ALLOWED_UNMANAGED_CREATE_TASK_FILES, (
        "Raw asyncio.create_task in routes/event bus must be replaced with a "
        "managed background task runner.\n"
        f"  Unexpected offenders : {offenders - ALLOWED_UNMANAGED_CREATE_TASK_FILES}\n"
        f"  Resolved (remove from allowlist): {ALLOWED_UNMANAGED_CREATE_TASK_FILES - offenders}"
    )


def test_uow_session_commit_only_in_uow_internals() -> None:
    """Direct uow.session.commit() calls must not appear outside the UoW implementation.

    Calling session.commit() directly bypasses the Unit-of-Work rollback
    handling.  Use `await uow.commit()` or the UoW context manager instead.
    """
    scan_roots = [
        SRC_ROOT / "app",
        SRC_ROOT / "api",
    ]

    offenders: set[str] = set()
    for root in scan_roots:
        for path in _python_files(root):
            if "uow.session.commit" in path.read_text(encoding="utf-8"):
                offenders.add(_relative(path))

    assert offenders == ALLOWED_UOW_SESSION_COMMIT_FILES, (
        "Use `await uow.commit()` or the UoW context manager; direct "
        "session.commit() bypasses rollback handling.\n"
        f"  Unexpected offenders : {offenders - ALLOWED_UOW_SESSION_COMMIT_FILES}\n"
        f"  Resolved (remove from allowlist): {ALLOWED_UOW_SESSION_COMMIT_FILES - offenders}"
    )
