import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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


async def test_seed_admin_user_creates_user(store: UserStore) -> None:
    await store.seed_admin_user("admin", "s3cureP@ss")
    user = await store.get_by_username("admin")
    assert user is not None
    assert user.username == "admin"
    from dbzap.auth.passwords import verify_password

    assert verify_password("s3cureP@ss", user.password_hash)


async def test_seed_admin_user_updates_password(store: UserStore) -> None:
    await store.seed_admin_user("admin", "old_password")
    user1 = await store.get_by_username("admin")
    old_hash = user1.password_hash

    await store.seed_admin_user("admin", "new_password")
    user2 = await store.get_by_username("admin")
    assert user2.password_hash != old_hash
    from dbzap.auth.passwords import verify_password

    assert verify_password("new_password", user2.password_hash)


async def test_seed_admin_user_idempotent(store: UserStore) -> None:
    await store.seed_admin_user("admin", "s3cureP@ss")
    user1 = await store.get_by_username("admin")
    await store.seed_admin_user("admin", "s3cureP@ss")
    user2 = await store.get_by_username("admin")
    from dbzap.auth.passwords import verify_password

    assert verify_password("s3cureP@ss", user1.password_hash)
    assert verify_password("s3cureP@ss", user2.password_hash)


async def test_seed_admin_user_handles_race(
    store: UserStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-7 / spec 06: under multi-worker startup, two workers can both
    observe ``get_by_username -> None`` and race to insert. The loser's
    ``create_user`` raises ``IntegrityError`` from the unique constraint;
    that MUST be swallowed and the loser should proceed to the
    "exists" branch (and update the password hash if needed).

    Simulated by forcing ``get_by_username`` to lie that the row is
    missing on the first call, and then running seed twice in
    succession — the second call hits IntegrityError on insert.
    """
    real_get = store.get_by_username
    call = {"n": 0}

    async def lying_get(username: str):
        call["n"] += 1
        if call["n"] <= 2:
            return None  # pretend nobody is there
        return await real_get(username)

    monkeypatch.setattr(store, "get_by_username", lying_get)

    # First seed actually creates the row.
    await store.seed_admin_user("admin", "first-password")

    # Second seed observes None (lying_get) → tries to insert → unique
    # constraint fires. MUST NOT re-raise.
    await store.seed_admin_user("admin", "second-password")

    user = await real_get("admin")
    assert user is not None
    from dbzap.auth.passwords import verify_password

    # The loser updated the hash to the new password.
    assert verify_password("second-password", user.password_hash)
