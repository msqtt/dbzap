import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.core.config import Settings
from dbzap.server.app import create_app


def _settings(**kwargs) -> Settings:  # type: ignore[no-untyped-def]
    defaults = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret_key": "test-health-secret",
        "explorer_username": "admin",
        "explorer_password": "s3cureP@ss",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
async def app() -> FastAPI:
    return await create_app(settings=_settings())


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# GET /healthz  (liveness)
# ---------------------------------------------------------------------------


async def test_liveness_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200


async def test_liveness_no_auth_required(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200


async def test_liveness_body_shape(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    data = resp.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], float)


async def test_liveness_content_type_json(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert "application/json" in resp.headers["content-type"]


async def test_liveness_no_checks_field(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert "checks" not in resp.json()


# ---------------------------------------------------------------------------
# GET /healthz/ready  (readiness)
# ---------------------------------------------------------------------------


async def test_readiness_returns_200_when_db_ok(client: AsyncClient) -> None:
    resp = await client.get("/healthz/ready")
    assert resp.status_code == 200


async def test_readiness_no_auth_required(client: AsyncClient) -> None:
    resp = await client.get("/healthz/ready")
    assert resp.status_code == 200


async def test_readiness_body_shape(client: AsyncClient) -> None:
    resp = await client.get("/healthz/ready")
    data = resp.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "uptime_seconds" in data
    assert "checks" in data
    assert "database" in data["checks"]
    db = data["checks"]["database"]
    assert db["status"] == "ok"
    assert "latency_ms" in db
    assert isinstance(db["latency_ms"], float)


async def test_readiness_503_when_db_unreachable() -> None:
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import create_async_engine

    from dbzap.core.introspector import SchemaIntrospector
    from dbzap.server.health import HealthCheck

    bad_engine = create_async_engine("sqlite+aiosqlite:////nonexistent/path/db.sqlite3")
    introspector = SchemaIntrospector(engine=bad_engine)
    hc = HealthCheck(
        engine=bad_engine,
        introspector=introspector,
        start_time=datetime.now(timezone.utc),
    )
    status_code, body = await hc.readiness()
    assert status_code == 503
    assert body["status"] == "error"
    assert body["checks"]["database"]["status"] == "error"
    assert "error" in body["checks"]["database"]
    await bad_engine.dispose()


# ---------------------------------------------------------------------------
# GET /healthz/detail  (authenticated)
# ---------------------------------------------------------------------------


async def test_detail_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/healthz/detail")
    assert resp.status_code == 401


async def test_detail_returns_200_with_valid_token(client: AsyncClient) -> None:
    login = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
    token = login.json()["access_token"]

    resp = await client.get("/healthz/detail", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


async def test_detail_body_shape(client: AsyncClient) -> None:
    login = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
    token = login.json()["access_token"]

    resp = await client.get("/healthz/detail", headers={"Authorization": f"Bearer {token}"})
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "api_mode" in data
    assert "checks" in data
    assert "database" in data["checks"]
    assert "introspection" in data
    assert "table_count" in data["introspection"]
    assert "last_reload" in data["introspection"]


async def test_uptime_is_positive(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.json()["uptime_seconds"] >= 0.0
