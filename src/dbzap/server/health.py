import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dbzap.core.introspector import SchemaIntrospector

_START_MONOTONIC = time.monotonic()
_START_WALL = datetime.now(timezone.utc)

_VERSION = "0.1.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uptime() -> float:
    return round(time.monotonic() - _START_MONOTONIC, 3)


class HealthCheck:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        introspector: SchemaIntrospector,
        start_time: datetime,
    ) -> None:
        self._engine = engine
        self._introspector = introspector
        self._start_monotonic = time.monotonic()

    async def liveness(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "timestamp": _now_iso(),
            "uptime_seconds": self._uptime(),
        }

    async def readiness(self) -> tuple[int, dict[str, Any]]:
        t0 = time.monotonic()
        try:
            async with asyncio.timeout(5.0):
                async with self._engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            latency_ms = round((time.monotonic() - t0) * 1000, 3)
            return 200, {
                "status": "ok",
                "timestamp": _now_iso(),
                "uptime_seconds": self._uptime(),
                "checks": {
                    "database": {"status": "ok", "latency_ms": latency_ms},
                },
            }
        except Exception as exc:
            return 503, {
                "status": "error",
                "timestamp": _now_iso(),
                "uptime_seconds": self._uptime(),
                "checks": {
                    "database": {"status": "error", "error": str(exc)},
                },
            }

    async def detail(self, api_mode: str) -> dict[str, Any]:
        t0 = time.monotonic()
        db_check: dict[str, Any]
        try:
            async with asyncio.timeout(5.0):
                async with self._engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            latency_ms = round((time.monotonic() - t0) * 1000, 3)
            pool = self._engine.pool
            db_check = {
                "status": "ok",
                "latency_ms": latency_ms,
                "pool_size": getattr(pool, "size", lambda: None)(),
                "pool_checked_out": getattr(pool, "checkedout", lambda: None)(),
                "pool_overflow": getattr(pool, "overflow", lambda: None)(),
            }
        except Exception as exc:
            db_check = {"status": "error", "error": str(exc)}

        try:
            tables = self._introspector.get_cached_schema()
            table_count = len(tables)
            last_reload = _now_iso()
        except RuntimeError:
            table_count = 0
            last_reload = None

        return {
            "status": "ok",
            "timestamp": _now_iso(),
            "uptime_seconds": self._uptime(),
            "version": _VERSION,
            "api_mode": api_mode,
            "checks": {"database": db_check},
            "introspection": {
                "table_count": table_count,
                "last_reload": last_reload,
            },
        }

    def _uptime(self) -> float:
        return round(time.monotonic() - self._start_monotonic, 3)


def create_health_router(
    *,
    health: HealthCheck,
    api_mode: str,
    get_current_user: Any,
) -> APIRouter:
    router = APIRouter(prefix="/healthz")

    @router.get("")
    async def liveness() -> dict[str, Any]:
        return await health.liveness()

    @router.get("/ready")
    async def readiness() -> Any:
        from fastapi import Response
        from fastapi.responses import JSONResponse

        status_code, body = await health.readiness()
        return JSONResponse(content=body, status_code=status_code)

    @router.get("/detail")
    async def detail(_user: Any = Depends(get_current_user)) -> dict[str, Any]:
        return await health.detail(api_mode=api_mode)

    return router
