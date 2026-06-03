import base64

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.auth.routes import create_auth_router
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings


def _settings(**kwargs) -> Settings:  # type: ignore[no-untyped-def]
    defaults = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret_key": "test-secret-for-routes",
        "explorer_username": "admin",
        "explorer_password": "s3cureP@ss",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


async def _make_client(engine: AsyncEngine, settings: Settings) -> AsyncClient:
    store = UserStore(engine=engine)
    await store.initialize()
    await store.seed_admin_user("admin", "s3cureP@ss")
    router = create_auth_router(store=store, settings=settings)
    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# JWT mode (default)
# ---------------------------------------------------------------------------


async def test_jwt_login_returns_token(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="jwt")) as client:
        resp = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"


async def test_jwt_me_with_bearer_token(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="jwt")) as client:
        login = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        token = login.json()["access_token"]
        resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"


async def test_jwt_wrong_password_401(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="jwt")) as client:
        resp = await client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401


async def test_jwt_basic_auth_rejected(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="jwt")) as client:
        cred = base64.b64encode(b"admin:s3cureP@ss").decode()
        resp = await client.get("/auth/me", headers={"Authorization": f"Basic {cred}"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Basic Auth mode
# ---------------------------------------------------------------------------


async def test_basic_login_not_available(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="basic")) as client:
        resp = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        assert resp.status_code == 404


async def test_basic_me_with_valid_credentials(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="basic")) as client:
        cred = base64.b64encode(b"admin:s3cureP@ss").decode()
        resp = await client.get("/auth/me", headers={"Authorization": f"Basic {cred}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"


async def test_basic_wrong_password_401(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="basic")) as client:
        cred = base64.b64encode(b"admin:wrongpass").decode()
        resp = await client.get("/auth/me", headers={"Authorization": f"Basic {cred}"})
        assert resp.status_code == 401


async def test_basic_unknown_user_401(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="basic")) as client:
        cred = base64.b64encode(b"nobody:s3cureP@ss").decode()
        resp = await client.get("/auth/me", headers={"Authorization": f"Basic {cred}"})
        assert resp.status_code == 401


async def test_basic_no_header_401(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="basic")) as client:
        resp = await client.get("/auth/me")
        assert resp.status_code == 401


async def test_basic_bearer_token_rejected(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="basic")) as client:
        resp = await client.get("/auth/me", headers={"Authorization": "Bearer some.jwt.token"})
        assert resp.status_code == 401


async def test_basic_password_with_colon(engine: AsyncEngine) -> None:
    """Password containing ':' should work — split on first ':' only."""
    async with AsyncClient(
        transport=ASGITransport(app=FastAPI()),
        base_url="http://test",
    ):
        # Use a fresh store with a password containing ':'
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            store = UserStore(engine=eng)
            await store.initialize()
            await store.seed_admin_user("admin", "pass:word")
            settings = _settings(auth_mode="basic")
            router = create_auth_router(store=store, settings=settings)
            app = FastAPI()
            app.include_router(router)
            c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            async with c:
                cred = base64.b64encode(b"admin:pass:word").decode()
                resp = await c.get("/auth/me", headers={"Authorization": f"Basic {cred}"})
                assert resp.status_code == 200
        finally:
            await eng.dispose()


# ---------------------------------------------------------------------------
# Both mode
# ---------------------------------------------------------------------------


async def test_both_login_available(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="both")) as client:
        resp = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        assert resp.status_code == 200


async def test_both_bearer_works(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="both")) as client:
        login = await client.post("/auth/login", json={"username": "admin", "password": "s3cureP@ss"})
        token = login.json()["access_token"]
        resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


async def test_both_basic_works(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings(auth_mode="both")) as client:
        cred = base64.b64encode(b"admin:s3cureP@ss").decode()
        resp = await client.get("/auth/me", headers={"Authorization": f"Basic {cred}"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


async def test_no_auth_header_401(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings()) as client:
        resp = await client.get("/auth/me")
        assert resp.status_code == 401


async def test_register_not_found(engine: AsyncEngine) -> None:
    async with await _make_client(engine, _settings()) as client:
        resp = await client.post("/auth/register", json={"username": "alice", "password": "s3cureP@ss"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Constant-time login (spec 06: prevent username enumeration via timing).
# ---------------------------------------------------------------------------


async def test_login_unknown_user_runs_dummy_bcrypt_verify(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown username MUST still trigger a bcrypt verify call.

    Without the dummy verify, the unknown-user branch returns ~100 ms
    earlier than the wrong-password branch and leaks user existence.
    """
    import dbzap.auth.routes as routes_mod

    call_count = {"verify": 0}

    real_verify = routes_mod.verify_password

    def counting_verify(plain: str, hashed: str) -> bool:
        call_count["verify"] += 1
        return real_verify(plain, hashed)

    monkeypatch.setattr(routes_mod, "verify_password", counting_verify)

    async with await _make_client(engine, _settings(auth_mode="jwt")) as client:
        resp = await client.post(
            "/auth/login",
            json={"username": "does-not-exist", "password": "anything"},
        )

    assert resp.status_code == 401
    assert call_count["verify"] >= 1, (
        "verify_password was never called on the unknown-user path — this is "
        "a timing oracle: attackers can enumerate usernames by response time"
    )


async def test_basic_auth_unknown_user_runs_dummy_bcrypt_verify(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Basic Auth MUST also run dummy verify on unknown users."""
    import dbzap.auth.dependencies as deps_mod

    call_count = {"verify": 0}

    real_verify = deps_mod.verify_password

    def counting_verify(plain: str, hashed: str) -> bool:
        call_count["verify"] += 1
        return real_verify(plain, hashed)

    monkeypatch.setattr(deps_mod, "verify_password", counting_verify)

    async with await _make_client(engine, _settings(auth_mode="basic")) as client:
        cred = base64.b64encode(b"does-not-exist:any").decode()
        resp = await client.get("/auth/me", headers={"Authorization": f"Basic {cred}"})

    assert resp.status_code == 401
    assert call_count["verify"] >= 1, (
        "verify_password was never called for unknown user via Basic Auth — "
        "timing oracle leaks user existence"
    )
