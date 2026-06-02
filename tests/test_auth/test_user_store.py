import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.auth.models import UserRecord
from dbzap.auth.user_store import UserStore


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


@pytest.fixture
async def store(engine: AsyncEngine) -> UserStore:
    s = UserStore(engine=engine)
    await s.initialize()
    return s


async def test_initialize_creates_table(store: UserStore, engine: AsyncEngine) -> None:
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.ext.asyncio import AsyncConnection

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: sa_inspect(c).get_table_names())
    assert "_users" in tables


async def test_create_user(store: UserStore) -> None:
    user = await store.create_user("alice", "hashed_pw")
    assert user.id is not None
    assert user.username == "alice"


async def test_get_by_username(store: UserStore) -> None:
    await store.create_user("bob", "hashed_pw")
    user = await store.get_by_username("bob")
    assert user is not None
    assert user.username == "bob"


async def test_get_by_username_missing_returns_none(store: UserStore) -> None:
    result = await store.get_by_username("nobody")
    assert result is None


async def test_get_by_id(store: UserStore) -> None:
    created = await store.create_user("carol", "hashed_pw")
    assert created.id is not None
    found = await store.get_by_id(created.id)
    assert found is not None
    assert found.username == "carol"


async def test_get_by_id_missing_returns_none(store: UserStore) -> None:
    result = await store.get_by_id(9999)
    assert result is None


async def test_duplicate_username_raises(store: UserStore) -> None:
    await store.create_user("dave", "pw1")
    with pytest.raises(Exception):
        await store.create_user("dave", "pw2")


async def test_password_hash_stored_not_plaintext(store: UserStore) -> None:
    from dbzap.auth.passwords import hash_password

    hashed = hash_password("s3cureP@ss")
    await store.create_user("eve", hashed)
    user = await store.get_by_username("eve")
    assert user is not None
    assert user.password_hash != "s3cureP@ss"
    assert user.password_hash == hashed
