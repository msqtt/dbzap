from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from dbzap.auth.models import UserRecord

_METADATA = MetaData()

_users_table = Table(
    "_users",
    _METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(255), unique=True, nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
)


class UserStore:
    def __init__(self, *, engine: AsyncEngine) -> None:
        self._engine = engine

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(_METADATA.create_all)

    async def seed_admin_user(self, username: str, password: str) -> None:
        """Create or update the admin user, race-safe under multi-worker startup.

        ``uvicorn --workers N`` launches N processes that each call this
        method on the same database. The naive sequence (lookup → branch
        on None → insert) has a window where two workers both miss and
        both insert; one wins and the other gets ``IntegrityError``.

        Pattern: try the insert first; on ``IntegrityError`` recover by
        re-reading the now-existing row and proceeding to the
        "user exists" branch (update hash if it no longer matches the
        configured password). See P0-7 / specs/06-auth.md.
        """
        from sqlalchemy.exc import IntegrityError

        from dbzap.auth.passwords import hash_password, verify_password

        pw_hash = hash_password(password)

        existing = await self.get_by_username(username)
        if existing is None:
            try:
                await self.create_user(username, pw_hash)
                return
            except IntegrityError:
                # A peer worker won the insert race. Fall through to the
                # "exists" branch using the freshly-readable row.
                existing = await self.get_by_username(username)
                if existing is None:
                    # Truly unexpected: insert claims to conflict but read
                    # finds nothing. Re-raise the original error indirectly
                    # by attempting one more insert so the caller sees a
                    # real failure rather than silent success.
                    raise

        if not verify_password(password, existing.password_hash):
            stmt = (
                update(_users_table)
                .where(_users_table.c.username == username)
                .values(password_hash=pw_hash)
            )
            async with self._engine.begin() as conn:
                await conn.execute(stmt)

    async def create_user(self, username: str, password_hash: str) -> UserRecord:
        stmt = insert(_users_table).values(username=username, password_hash=password_hash)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
            user_id = result.lastrowid
        if user_id is None:
            raise RuntimeError("Failed to insert user")
        return UserRecord(id=user_id, username=username, password_hash=password_hash)

    async def get_by_username(self, username: str) -> UserRecord | None:
        stmt = select(_users_table).where(_users_table.c.username == username)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()
        if row is None:
            return None
        return UserRecord(id=row.id, username=row.username, password_hash=row.password_hash)

    async def get_by_id(self, user_id: int) -> UserRecord | None:
        stmt = select(_users_table).where(_users_table.c.id == user_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()
        if row is None:
            return None
        return UserRecord(id=row.id, username=row.username, password_hash=row.password_hash)
