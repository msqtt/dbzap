from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _create_schema(conn: Connection) -> None:
    conn.execute(text("""
        CREATE TABLE users (
            id      INTEGER PRIMARY KEY,
            email   TEXT NOT NULL,
            name    TEXT,
            score   REAL DEFAULT 0.0,
            UNIQUE (email)
        )
    """))
    conn.execute(text("""
        CREATE TABLE posts (
            id      INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title   TEXT NOT NULL,
            body    TEXT
        )
    """))
    conn.execute(text("""
        CREATE TABLE post_tags (
            post_id INTEGER NOT NULL REFERENCES posts(id),
            tag     TEXT NOT NULL,
            PRIMARY KEY (post_id, tag),
            UNIQUE (tag, post_id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE audit_log (
            id      INTEGER PRIMARY KEY,
            message TEXT,
            created TEXT
        )
    """))


@pytest.fixture
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_create_schema)
    yield engine
    await engine.dispose()
