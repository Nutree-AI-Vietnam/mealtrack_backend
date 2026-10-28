"""
Health check endpoints for monitoring and status.
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.api.dependencies.auth import require_monitoring_access
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.schemas.response.subscription_access import (
    SubscriptionAccessAlertThresholdsResponse,
    SubscriptionAccessHealthReasonResponse,
    SubscriptionAccessHealthResponse,
    SubscriptionAccessMarkerCountsResponse,
)
from src.app.queries.subscription_access import GetSubscriptionAccessHealthQuery
from src.app.services.subscription_access_health import (
    HEALTH_OK,
    SubscriptionAccessHealthReport,
)
from src.infra.database.config_async import (
    _ASYNC_POOL_OVERFLOW,
    _ASYNC_POOL_SIZE,
    _ASYNC_POOL_TOTAL_CAPACITY,
    _IS_NEON_POOLER,
    _UVICORN_WORKERS,
    CONNECTION_MODE,
    async_engine,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Health"])
root_router = APIRouter(tags=["Health"])


def _deployment_info() -> dict[str, str | None]:
    """Expose non-sensitive deploy identity for staging/runtime verification."""
    return {
        "environment": os.getenv("ENVIRONMENT"),
        "render_service": os.getenv("RENDER_SERVICE_NAME"),
        "git_branch": os.getenv("GIT_BRANCH") or os.getenv("RENDER_GIT_BRANCH"),
        "git_commit": (
            os.getenv("GIT_SHA")
            or os.getenv("COMMIT_SHA")
            or os.getenv("RENDER_GIT_COMMIT")
            or os.getenv("SOURCE_VERSION")
        ),
        "app_version": os.getenv("APP_VERSION"),
    }


@root_router.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    """
    Basic health check endpoint for uptime monitoring.
    Supports HEAD for lightweight client connectivity probes.
    """
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "message": "API is running",
            "deployment": _deployment_info(),
        },
    )


@root_router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/", methods=["GET", "HEAD"])
async def root():
    """
    Root endpoint with API information.
    Supports HEAD for Render health checks.
    """
    return {
        "name": "MealTrack API",
        "version": "1.0.0",
        "description": "Meal tracking and nutritional analysis API",
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc",
        },
    }


@router.get("/health/db-pool")
async def database_pool_status(_monitor=Depends(require_monitoring_access)):
    """
    Inspect SQLAlchemy connection pool metrics.
    When using NullPool (Neon pooler), pool metrics are not available.
    """
    if _IS_NEON_POOLER:
        return {
            "status": "healthy",
            "connection_mode": CONNECTION_MODE,
            "pool_type": "NullPool",
            "worker_count": _UVICORN_WORKERS,
            "prepared_statement_cache_size": 0,
            "total_capacity": _ASYNC_POOL_TOTAL_CAPACITY,
            "note": (
                "Neon pooler (PgBouncer) handles connection reuse; "
                "SQLAlchemy checkout metrics are unavailable under NullPool"
            ),
        }

    try:
        if async_engine is None:
            raise RuntimeError("Async database engine is not initialized")
        pool = async_engine.sync_engine.pool
        checked_out = pool.checkedout()
        pool_size = pool.size()
        overflow = pool.overflow()
        available = max(pool_size - checked_out, 0)
        utilization_pct = (checked_out / pool_size) * 100 if pool_size > 0 else 0.0

        return {
            "status": "healthy",
            "connection_mode": CONNECTION_MODE,
            "pool_type": "QueuePool",
            "pool_size": pool_size,
            "max_overflow": _ASYNC_POOL_OVERFLOW,
            "worker_count": _UVICORN_WORKERS,
            "pool_size_per_worker": _ASYNC_POOL_SIZE,
            "checked_out": checked_out,
            "available": available,
            "overflow": overflow,
            "total_capacity": _ASYNC_POOL_TOTAL_CAPACITY,
            "utilization_pct": round(utilization_pct, 2),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": str(exc)},
        )


@router.get(
    "/health/subscription-access",
    response_model=SubscriptionAccessHealthResponse,
    responses={
        503: {
            "model": SubscriptionAccessHealthResponse,
            "description": "status degraded (thresholds exceeded, with the "
            "reasons), or status error when the counts could not be read",
        }
    },
)
async def subscription_access_health(
    _monitor=Depends(require_monitoring_access),
    event_bus=Depends(get_configured_event_bus),
):
    """Provider markers on RevenueCat rows against the alert thresholds.

    The uptime monitor polls this with X-Monitoring-Token and alerts on any
    non-200: 503 with status degraded when a backlog of rows left unresolved
    by failed RevenueCat reads grows or ages past its threshold
    (SUBSCRIPTION_ACCESS_ALERT_* env vars). Runbook:
    docs/runbooks/subscription-access-reconciliation.md, "Monitoring".
    """
    headers = {"Cache-Control": "no-store"}
    try:
        report: SubscriptionAccessHealthReport = await event_bus.send(
            GetSubscriptionAccessHealthQuery()
        )
    except Exception as exc:
        logger.warning(
            "Subscription access health could not be read: error_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error_type": type(exc).__name__},
            headers=headers,
        )
    counts = report.counts
    body = SubscriptionAccessHealthResponse(
        status=report.status,  # type: ignore[arg-type]
        checked_at=report.checked_at,
        counts=SubscriptionAccessMarkerCountsResponse(
            sync_required_users=counts.sync_required_users,
            stale_sync_required_users=counts.stale_sync_required_users,
            refund_pending_rows=counts.refund_pending_rows,
            stale_refund_pending_rows=counts.stale_refund_pending_rows,
            transfer_source_rows=counts.transfer_source_rows,
            oldest_marker_age_seconds=report.oldest_marker_age_seconds,
        ),
        thresholds=SubscriptionAccessAlertThresholdsResponse(
            sync_required_minutes=report.thresholds.sync_required_minutes,
            sync_required_users=report.thresholds.sync_required_users,
            refund_pending_minutes=report.thresholds.refund_pending_minutes,
            oldest_marker_hours=report.thresholds.oldest_marker_hours,
        ),
        reasons=[
            SubscriptionAccessHealthReasonResponse(
                code=reason.code,  # type: ignore[arg-type]
                message=reason.message,
            )
            for reason in report.reasons
        ],
    )
    return JSONResponse(
        status_code=200 if report.status == HEALTH_OK else 503,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


@router.get("/health/db-connections")
async def db_connection_status(_monitor=Depends(require_monitoring_access)):
    """
    Return active PostgreSQL connection counts.
    """
    try:
        stats = await _fetch_pg_connection_stats()
        return {"status": "healthy", **stats}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": str(exc)},
        )


async def _fetch_pg_connection_stats() -> dict[str, Any]:
    if async_engine is None:
        raise RuntimeError("Async database engine is not initialized")

    db_name = async_engine.url.database
    async with async_engine.connect() as connection:
        # pg_stat_activity works through PgBouncer (it's a regular SELECT)
        active_result = await connection.execute(
            text(
                "SELECT COUNT(*) FROM pg_stat_activity "
                "WHERE datname = :db_name AND state IS NOT NULL"
            ),
            {"db_name": db_name},
        )
        active_connections = active_result.scalar_one()

        # SHOW commands may fail through PgBouncer in transaction mode
        max_connections: int | None = None
        try:
            max_conn_row = (
                await connection.execute(text("SHOW max_connections"))
            ).fetchone()
            if max_conn_row:
                max_connections = int(max_conn_row[0])
        except Exception:
            pass

        utilization_pct: float | None = None
        if max_connections:
            utilization_pct = (active_connections / max_connections) * 100

        return {
            "active_connections": active_connections,
            "max_connections": max_connections,
            "utilization_pct": (
                round(utilization_pct, 2) if utilization_pct is not None else None
            ),
        }
