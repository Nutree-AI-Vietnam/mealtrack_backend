"""Admin-only memory snapshot for staging diagnosis.

Unauthenticated callers never reach the profiler. Production requests are
refused even when the caller is an admin and ``MEMPROF_ENABLED`` is set.
The route is omitted from the OpenAPI schema.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies.auth import require_admin
from src.api.ops.memprof import MemprofError, take_report

router = APIRouter(prefix="/v1/admin/memory", tags=["Admin"])


@router.get("/snapshot", include_in_schema=False)
async def memory_snapshot(_admin: str = Depends(require_admin)) -> dict:
    """Return this worker's top allocators and the diff since its last snapshot."""
    try:
        return await asyncio.to_thread(take_report)
    except MemprofError as exc:
        if exc.code == "production":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=exc.message,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
