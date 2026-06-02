from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, func, insert, select
from sqlalchemy.exc import IntegrityError
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

    async def create_user(self, username: str, password_hash: str) -> UserRecord:
        stmt = (
            insert(_users_table)
            .values(username=username, password_hash=password_hash)
            .returning(_users_table.c.id)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()
        if row is None:
            raise RuntimeError("Failed to insert user")
        return UserRecord(id=row[0], username=username, password_hash=password_hash)

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
