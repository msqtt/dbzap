import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.auth.dependencies import make_get_current_user
from dbzap.auth.routes import create_auth_router
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-secret-for-deps",
    )


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


@pytest.fixture
async def app(engine: AsyncEngine, settings: Settings) -> FastAPI:
    store = UserStore(engine=engine)
    await store.initialize()
    router = create_auth_router(store=store, settings=settings)
    get_current_user = make_get_current_user(store=store, settings=settings)

    from fastapi import Depends
    from dbzap.auth.models import UserRecord

    application = FastAPI()
    application.include_router(router)

    @application.get("/protected")
    async def protected(user: UserRecord = Depends(get_current_user)) -> dict:  # type: ignore[type-arg]
        return {"username": user.username}

    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _register_and_login(client: AsyncClient, username: str = "alice") -> str:
    await client.post("/auth/register", json={"username": username, "password": "s3cureP@ss"})
    resp = await client.post("/auth/login", json={"username": username, "password": "s3cureP@ss"})
    return resp.json()["access_token"]


async def test_protected_with_valid_token(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"


async def test_protected_without_token(client: AsyncClient) -> None:
    resp = await client.get("/protected")
    assert resp.status_code == 401


async def test_protected_malformed_token(client: AsyncClient) -> None:
    resp = await client.get("/protected", headers={"Authorization": "Bearer bad.tok.en"})
    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"].lower()


async def test_protected_missing_bearer_prefix(client: AsyncClient) -> None:
    resp = await client.get("/protected", headers={"Authorization": "Token abc"})
    assert resp.status_code == 401
