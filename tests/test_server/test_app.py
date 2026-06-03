import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from dbzap.core.config import Settings
from dbzap.server.app import create_app


def _settings(**kwargs) -> Settings:  # type: ignore[no-untyped-def]
    defaults = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret_key": "test-bootstrap-secret",
        "explorer_username": "admin",
        "explorer_password": "s3cureP@ss",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# App factory basics
# ---------------------------------------------------------------------------


async def test_create_app_returns_fastapi() -> None:
    app = await create_app(settings=_settings())
    assert isinstance(app, FastAPI)


async def test_auth_routes_always_mounted() -> None:
    for mode in ("rest", "graphql", "both"):
        app = await create_app(settings=_settings(api_mode=mode))
        routes = [r.path for r in app.routes]  # type: ignore[attr-defined]
        assert any("/auth/login" in p for p in routes), f"mode={mode} missing /auth/login"
        assert any("/auth/me" in p for p in routes), f"mode={mode} missing /auth/me"


# ---------------------------------------------------------------------------
# REST mode
# ---------------------------------------------------------------------------


async def test_rest_mode_no_graphql() -> None:
    app = await create_app(settings=_settings(api_mode="rest"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/graphql", json={"query": "{ __typename }"})
    assert resp.status_code == 404


async def test_graphql_mode_no_rest_prefix() -> None:
    app = await create_app(settings=_settings(api_mode="graphql"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/")
    assert resp.status_code == 404


async def test_both_mode_mounts_graphql() -> None:
    app = await create_app(settings=_settings(api_mode="both"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/graphql", json={"query": "{ __typename }"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


async def test_cors_header_present() -> None:
    app = await create_app(settings=_settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.options(
            "/auth/login",
            headers={"Origin": "http://example.com", "Access-Control-Request-Method": "POST"},
        )
    assert "access-control-allow-origin" in resp.headers


# ---------------------------------------------------------------------------
# _users table filtered from generators
# ---------------------------------------------------------------------------


async def test_users_internal_table_not_in_rest_routes() -> None:
    app = await create_app(settings=_settings(api_mode="rest"))
    paths = [r.path for r in app.routes]  # type: ignore[attr-defined]
    assert not any("/api/_users" in p for p in paths)


async def test_users_internal_table_not_in_graphql_schema() -> None:
    app = await create_app(settings=_settings(api_mode="graphql"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/graphql",
            json={"query": "{ __schema { queryType { fields { name } } } }"},
        )
    assert resp.status_code == 200
    fields = [f["name"] for f in resp.json()["data"]["__schema"]["queryType"]["fields"]]
    assert not any("_users" in name.lower() for name in fields)


# ---------------------------------------------------------------------------
# Auth flows work end-to-end through the assembled app
# ---------------------------------------------------------------------------


async def test_seeded_admin_can_login() -> None:
    app = await create_app(settings=_settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        assert login.status_code == 200
        assert "access_token" in login.json()


async def test_openapi_json_requires_auth() -> None:
    app = await create_app(settings=_settings(api_mode="rest"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/openapi.json")
    assert resp.status_code == 401


async def test_openapi_json_returns_schema_when_authenticated() -> None:
    app = await create_app(settings=_settings(api_mode="rest"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        token = login.json()["access_token"]
        resp = await c.get("/openapi.json", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("openapi", "").startswith("3")


async def test_missing_jwt_secret_raises_at_startup() -> None:
    with pytest.raises(Exception):
        Settings(database_url="sqlite+aiosqlite:///:memory:", jwt_secret_key="")
