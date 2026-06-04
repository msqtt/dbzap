
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

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
    from dbzap.core.introspector import SchemaIntrospector
    from dbzap.server.health import HealthCheck

    bad_engine = create_async_engine("sqlite+aiosqlite:////nonexistent/path/db.sqlite3")
    introspector = SchemaIntrospector(engine=bad_engine)
    hc = HealthCheck(
        engine=bad_engine,
        introspector=introspector,
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


# ---------------------------------------------------------------------------
# /healthz/detail introspection.last_reload — must reflect the introspector's
# actual last reload, not the current time at the call site (spec 09).
# ---------------------------------------------------------------------------


async def test_detail_last_reload_reflects_introspector_state(client: AsyncClient) -> None:
    """``introspection.last_reload`` MUST come from the introspector,
    NOT be fabricated by recomputing ``datetime.now()`` on every call."""
    import asyncio

    # First call to /healthz/detail (after auth)
    login = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r1 = await client.get("/healthz/detail", headers=headers)
    assert r1.status_code == 200
    first = r1.json()["introspection"]["last_reload"]

    # Wait a moment, then call again. last_reload must NOT change just
    # because we polled — schema didn't reload.
    await asyncio.sleep(0.1)

    r2 = await client.get("/healthz/detail", headers=headers)
    second = r2.json()["introspection"]["last_reload"]

    assert first == second, (
        f"last_reload changed between calls without an actual reload "
        f"({first!r} -> {second!r}); endpoint is fabricating the timestamp"
    )
