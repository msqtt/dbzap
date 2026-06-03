from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.auth.dependencies import make_get_current_user
from dbzap.auth.models import UserRecord
from dbzap.auth.routes import create_auth_router
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings, get_settings
from dbzap.core.introspector import SchemaIntrospector
from dbzap.generators.graphql import GraphqlApiGenerator
from dbzap.generators.rest import RestApiGenerator
from dbzap.server.health import HealthCheck, create_health_router
from dbzap.server.metrics import MetricsCollector, create_metrics_router
from dbzap.server.middleware import PerformanceMiddleware

logger: Any = structlog.get_logger(__name__)

_INTERNAL_TABLES = {"_users"}
_STATIC_DIR = Path(__file__).parent / "static"

_SYNC_TO_ASYNC_SCHEMES = {
    "mysql": "mysql+aiomysql",
    "postgres": "postgresql+asyncpg",
    "postgresql": "postgresql+asyncpg",
}


def _normalize_db_url(url: str) -> str:
    for sync_scheme, async_scheme in _SYNC_TO_ASYNC_SCHEMES.items():
        prefix = f"{sync_scheme}://"
        if url.startswith(prefix):
            return async_scheme + url[len(sync_scheme):]
    return url


def _parse_timeout_ms(value: str) -> int | None:
    """Parse a duration string like ``5s`` / ``500ms`` / ``5`` into milliseconds.

    Returns None for empty / invalid values so callers can skip configuration.
    """
    if not value:
        return None
    v = value.strip().lower()
    try:
        if v.endswith("ms"):
            return max(0, int(float(v[:-2].strip())))
        if v.endswith("s"):
            return max(0, int(float(v[:-1].strip()) * 1000))
        return max(0, int(float(v) * 1000))
    except (ValueError, TypeError):
        return None


def _build_engine(cfg: Settings) -> AsyncEngine:
    """Build the async engine with dialect-aware pool sizing and timeouts.

    SQLite uses ``StaticPool`` and rejects ``pool_size``/``max_overflow``-style
    keyword arguments, so they are only forwarded for server databases.
    """
    url = _normalize_db_url(cfg.database_url)
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    timeout_ms = _parse_timeout_ms(cfg.db_statement_timeout)

    if url.startswith("postgresql"):
        kwargs["pool_size"] = cfg.db_pool_size
        kwargs["max_overflow"] = cfg.db_max_overflow
        kwargs["pool_timeout"] = cfg.db_pool_timeout
        kwargs["pool_recycle"] = cfg.db_pool_recycle
        if timeout_ms:
            # asyncpg honors `server_settings` to apply per-connection GUCs.
            kwargs["connect_args"] = {
                "server_settings": {"statement_timeout": str(timeout_ms)}
            }
    elif url.startswith("mysql"):
        kwargs["pool_size"] = cfg.db_pool_size
        kwargs["max_overflow"] = cfg.db_max_overflow
        kwargs["pool_timeout"] = cfg.db_pool_timeout
        kwargs["pool_recycle"] = cfg.db_pool_recycle
        if timeout_ms:
            # aiomysql executes ``init_command`` on every new connection.
            kwargs["connect_args"] = {
                "init_command": f"SET SESSION MAX_EXECUTION_TIME={timeout_ms}"
            }
    # SQLite: keep the default StaticPool; pool sizing flags would raise.

    return create_async_engine(url, **kwargs)


async def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    engine = _build_engine(cfg)

    introspector = SchemaIntrospector(engine=engine)
    try:
        all_tables = await introspector.introspect()
    except ConnectionError as exc:
        logger.error("database_unreachable", error=str(exc))
        raise

    tables = [t for t in all_tables if t.name not in _INTERNAL_TABLES]

    store = UserStore(engine=engine)
    await store.initialize()

    if cfg.explorer_username and cfg.explorer_password:
        await store.seed_admin_user(cfg.explorer_username, cfg.explorer_password)

    collector = MetricsCollector()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(lifespan=lifespan)

    # PerformanceMiddleware must come before GZip to measure uncompressed time
    app.add_middleware(PerformanceMiddleware, collector=collector)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    get_current_user = make_get_current_user(store=store, settings=cfg)

    health = HealthCheck(
        engine=engine,
        introspector=introspector,
        start_time=datetime.now(timezone.utc),
    )
    health_router = create_health_router(
        health=health,
        api_mode=cfg.api_mode,
        get_current_user=get_current_user,
    )
    app.include_router(health_router)

    def _pool_stats() -> tuple[int, int, int]:
        pool = engine.pool
        size_fn = getattr(pool, "size", None)
        co_fn = getattr(pool, "checkedout", None)
        of_fn = getattr(pool, "overflow", None)
        size = int(size_fn() or 0) if callable(size_fn) else 0
        checked_out = int(co_fn() or 0) if callable(co_fn) else 0
        overflow_val = int(of_fn() or 0) if callable(of_fn) else 0
        return size, checked_out, max(0, overflow_val)

    app.include_router(create_metrics_router(collector, pool_stats_provider=_pool_stats))

    auth_router = create_auth_router(store=store, settings=cfg)
    app.include_router(auth_router)

    if cfg.api_mode in ("rest", "both"):
        rest_gen = RestApiGenerator(engine=engine)
        rest_gen.generate(app, tables)

    if cfg.api_mode in ("graphql", "both"):
        gql_gen = GraphqlApiGenerator(engine=engine)
        schema = gql_gen.generate(tables)
        gql_gen.mount(app, schema)

    if cfg.enable_explorer and _STATIC_DIR.exists():
        app.mount("/explorer/static", StaticFiles(directory=_STATIC_DIR), name="explorer-static")

        @app.get("/explorer", response_class=FileResponse, include_in_schema=False)
        async def explorer_index() -> FileResponse:
            return FileResponse(_STATIC_DIR / "index.html")

        @app.get("/explorer/config", include_in_schema=False)
        async def explorer_config() -> dict[str, str | None]:
            return {"username": cfg.explorer_username, "password": cfg.explorer_password}

    # Protect /openapi.json behind authentication
    original_openapi = app.openapi
    # Remove the default openapi route added by FastAPI setup
    app.router.routes = [
        r for r in app.router.routes if getattr(r, 'path', None) != '/openapi.json'
    ]

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_with_auth(user: UserRecord = Depends(get_current_user)) -> JSONResponse:  # type: ignore[misc]
        return JSONResponse(original_openapi())

    return app
