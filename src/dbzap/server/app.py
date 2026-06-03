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

from dbzap.auth.dependencies import make_get_current_user
from dbzap.auth.models import UserRecord
from dbzap.auth.routes import create_auth_router
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings, get_settings
from dbzap.core.engine import build_engine
from dbzap.core.introspector import SchemaIntrospector
from dbzap.generators.graphql import GraphqlApiGenerator
from dbzap.generators.rest import RestApiGenerator
from dbzap.server.health import HealthCheck, create_health_router
from dbzap.server.metrics import MetricsCollector, create_metrics_router
from dbzap.server.middleware import PerformanceMiddleware

logger: Any = structlog.get_logger(__name__)

_INTERNAL_TABLES = {"_users"}
_STATIC_DIR = Path(__file__).parent / "static"


async def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    engine = build_engine(cfg)

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
