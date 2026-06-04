import asyncio
import importlib.metadata
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dbzap.core.introspector import SchemaIntrospector

try:
    _VERSION = importlib.metadata.version("dbzap")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "0.0.0-dev"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class HealthCheck:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        introspector: SchemaIntrospector,
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
        except RuntimeError:
            table_count = 0

        last_reload_at = self._introspector.last_reload_at
        last_reload = last_reload_at.isoformat() if last_reload_at is not None else None

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
        import orjson
        from fastapi import Response

        status_code, body = await health.readiness()
        return Response(
            content=orjson.dumps(body),
            status_code=status_code,
            media_type="application/json",
        )

    @router.get("/detail")
    async def detail(_user: Any = Depends(get_current_user)) -> dict[str, Any]:
        return await health.detail(api_mode=api_mode)

    return router
