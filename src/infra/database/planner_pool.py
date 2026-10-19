"""Observe acquisition wait without changing SQLAlchemy's pool policy."""

import asyncio

from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from src.planner_observability import planner_phase


class _CancelSafeTerminateMixin:
    """Check a connection back in when its terminate is cancelled (PYTHON-JZ).

    A client disconnect cancels a streaming request through an anyio cancel
    scope, which cancels every later await in that task as well. If the cancel
    lands during the pool pre-ping at checkout, SQLAlchemy invalidates the
    connection, and asyncpg's graceful close inside ``terminate()`` is
    cancelled again. ``terminate()`` force-closes the socket and then re-raises
    that ``CancelledError``, which skipped the checkin of the record, so the
    slot stayed checked out until the garbage collector found it.

    The socket is already closed when that error arrives, so the invalidation
    is allowed to finish. The cancellation that started the invalidation is
    still raised by SQLAlchemy afterwards. Pool size and limits are unchanged.
    """

    def _close_connection(self, connection, *, terminate: bool = False) -> None:
        try:
            super()._close_connection(connection, terminate=terminate)
        except asyncio.CancelledError:
            if not terminate:
                raise


class PlannerQueuePool(_CancelSafeTerminateMixin, AsyncAdaptedQueuePool):
    def _do_get(self):
        # Includes local queue wait and a new connection's establishment when
        # necessary. SQL execution and transaction hold time are separate spans.
        with planner_phase("checkout"):
            return super()._do_get()


class PlannerNullPool(_CancelSafeTerminateMixin, NullPool):
    def _do_get(self):
        with planner_phase("checkout"):
            return super()._do_get()
