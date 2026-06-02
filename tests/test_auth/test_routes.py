import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.auth.routes import create_auth_router
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-secret-for-routes",
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
    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


async def test_register_creates_user(client: AsyncClient) -> None:
    resp = await client.post("/auth/register", json={"username": "alice", "password": "s3cureP@ss"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "alice"
    assert "id" in data
    assert "password" not in data
    assert "password_hash" not in data


async def test_register_duplicate_returns_409(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"username": "alice", "password": "s3cureP@ss"})
    resp = await client.post("/auth/register", json={"username": "alice", "password": "s3cureP@ss"})
    assert resp.status_code == 409


async def test_register_short_password_returns_422(client: AsyncClient) -> None:
    resp = await client.post("/auth/register", json={"username": "alice", "password": "short"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_returns_token(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"username": "bob", "password": "s3cureP@ss"})
    resp = await client.post("/auth/login", json={"username": "bob", "password": "s3cureP@ss"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0


async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"username": "bob", "password": "s3cureP@ss"})
    resp = await client.post("/auth/login", json={"username": "bob", "password": "wrongpass"})
    assert resp.status_code == 401


async def test_login_unknown_user_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/auth/login", json={"username": "nobody", "password": "s3cureP@ss"})
    assert resp.status_code == 401


async def test_login_same_error_message_for_wrong_pw_and_unknown(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"username": "bob", "password": "s3cureP@ss"})
    resp_wrong = await client.post("/auth/login", json={"username": "bob", "password": "wrong"})
    resp_unknown = await client.post("/auth/login", json={"username": "nobody", "password": "wrong"})
    assert resp_wrong.json()["detail"] == resp_unknown.json()["detail"]


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


async def test_me_returns_user(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"username": "carol", "password": "s3cureP@ss"})
    login = await client.post("/auth/login", json={"username": "carol", "password": "s3cureP@ss"})
    token = login.json()["access_token"]
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "carol"


async def test_me_no_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


async def test_me_invalid_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert resp.status_code == 401


async def test_me_expired_token_returns_401(client: AsyncClient, settings: Settings) -> None:
    import time

    from dbzap.auth.tokens import create_access_token

    token = create_access_token(
        {"sub": "1"},
        secret=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        expire_minutes=0,
    )
    time.sleep(1)
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()
