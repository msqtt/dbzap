import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class TestSchemaIntrospector:
    async def test_introspect_returns_all_tables(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        names = {t.name for t in tables}
        assert {"users", "posts", "post_tags", "audit_log"} == names

    async def test_introspect_column_names_users(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        col_names = [c.name for c in users.columns]
        assert "id" in col_names
        assert "email" in col_names
        assert "name" in col_names
        assert "score" in col_names

    async def test_introspect_non_nullable_column(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        email_col = next(c for c in users.columns if c.name == "email")
        assert email_col.nullable is False

    async def test_introspect_nullable_column(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        name_col = next(c for c in users.columns if c.name == "name")
        assert name_col.nullable is True

    async def test_introspect_column_default(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        score_col = next(c for c in users.columns if c.name == "score")
        assert score_col.default is not None

    async def test_introspect_single_primary_key(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        assert users.primary_key == ["id"]

    async def test_introspect_composite_primary_key(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        post_tags = next(t for t in tables if t.name == "post_tags")
        assert len(post_tags.primary_key) == 2
        assert "post_id" in post_tags.primary_key
        assert "tag" in post_tags.primary_key

    async def test_introspect_foreign_key(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        posts = next(t for t in tables if t.name == "posts")
        assert len(posts.foreign_keys) >= 1
        fk = next(f for f in posts.foreign_keys if f.source_column == "user_id")
        assert fk.target_table == "users"
        assert fk.target_column == "id"

    async def test_introspect_unique_constraint_single_col(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        assert ["email"] in users.unique_constraints

    async def test_introspect_unique_constraint_multi_col(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        post_tags = next(t for t in tables if t.name == "post_tags")
        assert len(post_tags.unique_constraints) >= 1
        # multi-col UQ (tag, post_id)
        assert any(len(uq) == 2 for uq in post_tags.unique_constraints)

    async def test_introspect_column_is_unique_true(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        email_col = next(c for c in users.columns if c.name == "email")
        assert email_col.is_unique is True

    async def test_introspect_column_is_unique_false(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        tables = await introspector.introspect()
        users = next(t for t in tables if t.name == "users")
        name_col = next(c for c in users.columns if c.name == "name")
        assert name_col.is_unique is False

    async def test_introspect_zero_tables(self) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        empty_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            introspector = SchemaIntrospector(engine=empty_engine)
            tables = await introspector.introspect()
            assert tables == []
        finally:
            await empty_engine.dispose()

    async def test_introspect_caches_result(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        result1 = await introspector.introspect()
        result2 = await introspector.introspect()
        assert result1 is result2

    async def test_get_cached_schema_raises_before_introspect(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        with pytest.raises(RuntimeError):
            introspector.get_cached_schema()

    async def test_get_cached_schema_returns_cache(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        populated = await introspector.introspect()
        cached = introspector.get_cached_schema()
        assert cached is populated

    async def test_reload_clears_and_repopulates_cache(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        introspector = SchemaIntrospector(engine=async_engine)
        first = await introspector.introspect()
        reloaded = await introspector.reload()
        assert isinstance(reloaded, list)
        assert len(reloaded) == len(first)
        # reload returns a fresh list object
        assert reloaded is not first

    async def test_introspect_table_single_table(self, async_engine: AsyncEngine) -> None:
        from dbzap.core.introspector import SchemaIntrospector, TableInfo

        introspector = SchemaIntrospector(engine=async_engine)
        result = await introspector.introspect_table("users")
        assert isinstance(result, TableInfo)
        assert result.name == "users"
        assert len(result.columns) > 0

    async def test_connection_error_raises_connection_error(self) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        bad_engine = create_async_engine(
            "postgresql+asyncpg://bad:secret@127.0.0.1:19999/nope",
        )
        try:
            introspector = SchemaIntrospector(engine=bad_engine)
            with pytest.raises(ConnectionError):
                await introspector.introspect()
        finally:
            await bad_engine.dispose()

    async def test_connection_error_masks_password(self) -> None:
        from dbzap.core.introspector import SchemaIntrospector

        bad_engine = create_async_engine(
            "postgresql+asyncpg://user:supersecret@127.0.0.1:19999/nope",
        )
        try:
            introspector = SchemaIntrospector(engine=bad_engine)
            with pytest.raises(ConnectionError) as exc_info:
                await introspector.introspect()
            assert "supersecret" not in str(exc_info.value)
        finally:
            await bad_engine.dispose()
