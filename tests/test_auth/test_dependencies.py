import base64

import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.auth.dependencies import make_get_current_user
from dbzap.auth.models import UserRecord
from dbzap.auth.routes import create_auth_router
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-secret-for-deps",
        explorer_username="admin",
        explorer_password="s3cureP@ss",
    )


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


async def _make_app(engine: AsyncEngine, settings: Settings) -> FastAPI:
    store = UserStore(engine=engine)
    await store.initialize()
    await store.seed_admin_user("admin", "s3cureP@ss")
    router = create_auth_router(store=store, settings=settings)
    get_current_user = make_get_current_user(store=store, settings=settings)

    application = FastAPI()
    application.include_router(router)

    @application.get("/protected")
    async def protected(user: UserRecord = Depends(get_current_user)) -> dict:  # type: ignore[type-arg]
        return {"username": user.username}

    return application


async def test_protected_with_bearer_token(engine: AsyncEngine, settings: Settings) -> None:
    app = await _make_app(engine, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        token = login.json()["access_token"]
        resp = await c.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"


async def test_protected_with_basic_auth(engine: AsyncEngine) -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-secret-for-deps",
        auth_mode="basic",
        explorer_username="admin",
        explorer_password="s3cureP@ss",
    )
    app = await _make_app(engine, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        cred = base64.b64encode(b"admin:s3cureP@ss").decode()
        resp = await c.get("/protected", headers={"Authorization": f"Basic {cred}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"


async def test_protected_without_token(engine: AsyncEngine, settings: Settings) -> None:
    app = await _make_app(engine, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/protected")
        assert resp.status_code == 401


async def test_protected_malformed_token(engine: AsyncEngine, settings: Settings) -> None:
    app = await _make_app(engine, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/protected", headers={"Authorization": "Bearer bad.tok.en"})
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()
