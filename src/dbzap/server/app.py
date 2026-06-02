from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.ext.asyncio import create_async_engine

from dbzap.auth.dependencies import make_get_current_user
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


async def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    engine = create_async_engine(
        cfg.database_url,
        pool_pre_ping=True,
    )

    introspector = SchemaIntrospector(engine=engine)
    try:
        all_tables = await introspector.introspect()
    except ConnectionError as exc:
        logger.error("database_unreachable", error=str(exc))
        raise

    tables = [t for t in all_tables if t.name not in _INTERNAL_TABLES]

    store = UserStore(engine=engine)
    await store.initialize()

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
    app.include_router(create_metrics_router(collector))

    auth_router = create_auth_router(store=store, settings=cfg)
    app.include_router(auth_router)

    if cfg.api_mode in ("rest", "both"):
        rest_gen = RestApiGenerator(engine=engine)
        rest_gen.generate(app, tables)

    if cfg.api_mode in ("graphql", "both"):
        gql_gen = GraphqlApiGenerator(engine=engine)
        schema = gql_gen.generate(tables)
        gql_gen.mount(app, schema)

    return app
