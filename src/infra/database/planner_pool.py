"""Observe acquisition wait without changing SQLAlchemy's pool policy."""

from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from src.planner_observability import planner_phase


class PlannerQueuePool(AsyncAdaptedQueuePool):
    def _do_get(self):
        # Includes local queue wait and a new connection's establishment when
        # necessary. SQL execution and transaction hold time are separate spans.
        with planner_phase("checkout"):
            return super()._do_get()


class PlannerNullPool(NullPool):
    def _do_get(self):
        with planner_phase("checkout"):
            return super()._do_get()
