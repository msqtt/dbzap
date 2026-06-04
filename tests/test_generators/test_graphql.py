"""Tests for GraphQL API generator (spec: 05-graphql-api-generator.md, 09-graphql-relay-filtering.md)."""
from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import AsyncGenerator

import pytest
import strawberry
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.core.introspector import SchemaIntrospector, TableInfo
from dbzap.generators.graphql import GraphqlApiGenerator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_schema(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE users (
                id    INTEGER PRIMARY KEY,
                name  TEXT    NOT NULL,
                email TEXT    NOT NULL UNIQUE,
                score REAL    DEFAULT 0.0
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE posts (
                id      INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                title   TEXT    NOT NULL,
                body    TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE audit_log (
                message TEXT,
                level   TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE order_items (
                order_id INTEGER NOT NULL,
                item_id  INTEGER NOT NULL,
                qty      INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (order_id, item_id)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE tags (
                slug TEXT PRIMARY KEY,
                label TEXT NOT NULL
            )
            """
        )
    )


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(_build_schema)
    yield eng
    await eng.dispose()


@pytest.fixture
async def tables(engine: AsyncEngine) -> list[TableInfo]:
    return await SchemaIntrospector(engine=engine).introspect()


@pytest.fixture
def generator(engine: AsyncEngine) -> GraphqlApiGenerator:
    return GraphqlApiGenerator(engine=engine)


@pytest.fixture
async def app(engine: AsyncEngine, tables: list[TableInfo], generator: GraphqlApiGenerator) -> FastAPI:
    fa = FastAPI()
    schema = generator.generate(tables)
    generator.mount(fa, schema)
    return fa


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _gql(client: AsyncClient, query: str, variables: dict | None = None) -> dict:
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = await client.post("/graphql", json=payload)
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Schema structure
# ---------------------------------------------------------------------------


class TestSchemaStructure:
    def test_generate_returns_schema(self, generator: GraphqlApiGenerator, tables: list[TableInfo]) -> None:
        schema = generator.generate(tables)
        assert isinstance(schema, strawberry.Schema)

    def test_empty_tables_produces_valid_schema(self, generator: GraphqlApiGenerator) -> None:
        schema = generator.generate([])
        assert isinstance(schema, strawberry.Schema)

    def test_mount_adds_graphql_route(self, app: FastAPI) -> None:
        paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert any("/graphql" in p for p in paths)

    def test_relay_connection_types_exist(self, generator: GraphqlApiGenerator, tables: list[TableInfo]) -> None:
        schema = generator.generate(tables)
        schema_str = str(schema)
        assert "PageInfo" in schema_str
        assert "UsersConnection" in schema_str
        assert "UsersEdge" in schema_str
        assert "UsersFilter" in schema_str

    def test_successive_generate_calls_are_isolated(
        self, engine: AsyncEngine, tables: list[TableInfo]
    ) -> None:
        """Two independent generators must NOT pollute a shared module
        namespace. Successive calls (different table sets, multi-tenant
        setups, repeated test fixtures) MUST produce schemas that don't
        bleed types into each other.
        """

        import dbzap.generators.graphql as graphql_mod

        before = set(graphql_mod.__dict__.keys())

        gen_a = GraphqlApiGenerator(engine=engine)
        gen_a.generate(tables)

        gen_b = GraphqlApiGenerator(engine=engine)
        gen_b.generate(tables[:1])  # smaller schema

        after = set(graphql_mod.__dict__.keys())
        leaked = after - before
        # Filter out anything pre-existing or from imports
        suspicious = {
            name for name in leaked
            if not name.startswith("_")
            and name not in {"sys"}  # stdlib leaks via lazy import are fine
        }
        assert not suspicious, (
            f"GraphqlApiGenerator leaked dynamically-built types into "
            f"sys.modules['{graphql_mod.__name__}']: {sorted(suspicious)}. "
            "Successive generate() calls share state and silently overwrite types."
        )


# ---------------------------------------------------------------------------
# Query – list (Relay Connection)
# ---------------------------------------------------------------------------


class TestListQuery:
    async def test_list_users_returns_empty_initially(self, client: AsyncClient) -> None:
        result = await _gql(client, "{ users { edges { node { id name email } } } }")
        assert result.get("errors") is None
        assert result["data"]["users"]["edges"] == []

    async def test_list_users_returns_inserted_rows(self, client: AsyncClient) -> None:
        await _gql(
            client,
            'mutation { createUsers(input: { name: "Alice", email: "a@x.com" }) { id } }',
        )
        result = await _gql(client, "{ users { edges { node { name } } } }")
        assert result["data"]["users"]["edges"][0]["node"]["name"] == "Alice"

    async def test_list_pagination_first(self, client: AsyncClient) -> None:
        for i in range(5):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "U{i}", email: "u{i}@x.com" }}) {{ id }} }}',
            )
        result = await _gql(client, "{ users(first: 2) { edges { node { id } } pageInfo { hasNextPage } } }")
        assert len(result["data"]["users"]["edges"]) == 2
        assert result["data"]["users"]["pageInfo"]["hasNextPage"] is True

    async def test_list_pagination_forward_with_after(self, client: AsyncClient) -> None:
        for i in range(4):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "U{i}", email: "off{i}@x.com" }}) {{ id }} }}',
            )
        all_edges = (
            await _gql(client, "{ users(first: 4) { edges { node { id } cursor } pageInfo { hasNextPage } } }")
        )["data"]["users"]["edges"]
        cursor = all_edges[1]["cursor"]
        result = await _gql(
            client,
            f'{{ users(first: 2, after: "{cursor}") {{ edges {{ node {{ id }} }} }} }}',
        )
        edges = result["data"]["users"]["edges"]
        assert edges[0]["node"]["id"] == all_edges[2]["node"]["id"]

    async def test_list_first_clamped_to_100(self, client: AsyncClient) -> None:
        result = await _gql(client, "{ users(first: 9999) { edges { node { id } } } }")
        assert result.get("errors") is None

    async def test_list_total_count(self, client: AsyncClient) -> None:
        for i in range(3):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "T{i}", email: "t{i}@x.com" }}) {{ id }} }}',
            )
        result = await _gql(client, "{ users { totalCount edges { node { id } } } }")
        assert result["data"]["users"]["totalCount"] == 3

    async def test_total_count_reflects_filter(self, client: AsyncClient) -> None:
        """Bug 4 / spec 09: totalCount must reflect filter, not full table size."""
        for i in range(5):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "Xray{i}", email: "x{i}@x.com" }}) {{ id }} }}',
            )
        for i in range(3):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "Yankee{i}", email: "y{i}@y.com" }}) {{ id }} }}',
            )

        result = await _gql(
            client,
            '{ users(filter: { name: { startsWith: "Xray" } }) { totalCount } }',
        )
        # Should be 5 (filtered), NOT 8 (full table count).
        assert result["data"]["users"]["totalCount"] == 5

    async def test_total_count_reflects_search(self, client: AsyncClient) -> None:
        """Bug 4 / spec 09: totalCount must reflect global search, not full table."""
        for i in range(3):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "Alice{i}", email: "alice{i}@x.com" }}) {{ id }} }}',
            )
        for i in range(2):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "Bob{i}", email: "bob{i}@x.com" }}) {{ id }} }}',
            )

        result = await _gql(client, '{ users(search: "alice") { totalCount } }')
        assert result["data"]["users"]["totalCount"] == 3


# ---------------------------------------------------------------------------
# Query – filtering
# ---------------------------------------------------------------------------


class TestListFiltering:
    async def test_filter_by_eq(self, client: AsyncClient) -> None:
        for i in range(3):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "F{i}", email: "f{i}@x.com" }}) {{ id }} }}',
            )
        result = await _gql(
            client,
            '{ users(filter: { name: { eq: "F1" } }) { edges { node { name } } } }',
        )
        assert len(result["data"]["users"]["edges"]) == 1
        assert result["data"]["users"]["edges"][0]["node"]["name"] == "F1"

    async def test_filter_by_contains(self, client: AsyncClient) -> None:
        await _gql(
            client,
            'mutation { createUsers(input: { name: "Alice Wonderland", email: "alice@x.com" }) { id } }',
        )
        await _gql(
            client,
            'mutation { createUsers(input: { name: "Bob", email: "bob@x.com" }) { id } }',
        )
        result = await _gql(
            client,
            '{ users(filter: { name: { contains: "Wonder" } }) { edges { node { name } } } }',
        )
        assert len(result["data"]["users"]["edges"]) == 1
        assert result["data"]["users"]["edges"][0]["node"]["name"] == "Alice Wonderland"

    async def test_search_across_string_columns(self, client: AsyncClient) -> None:
        await _gql(
            client,
            'mutation { createUsers(input: { name: "Charlie", email: "charlie@test.com" }) { id } }',
        )
        await _gql(
            client,
            'mutation { createUsers(input: { name: "Dana", email: "dana@other.com" }) { id } }',
        )
        result = await _gql(
            client,
            '{ users(search: "charlie") { edges { node { name } } } }',
        )
        assert len(result["data"]["users"]["edges"]) == 1
        assert result["data"]["users"]["edges"][0]["node"]["name"] == "Charlie"


# ---------------------------------------------------------------------------
# Query – byId
# ---------------------------------------------------------------------------


class TestByIdQuery:
    async def test_get_existing_row(self, client: AsyncClient) -> None:
        created = (
            await _gql(
                client,
                'mutation { createUsers(input: { name: "Bob", email: "bob@x.com" }) { id } }',
            )
        )["data"]["createUsers"]
        result = await _gql(
            client,
            f'{{ usersById(id: {created["id"]}) {{ name email }} }}',
        )
        assert result["data"]["usersById"]["name"] == "Bob"

    async def test_get_nonexistent_returns_null(self, client: AsyncClient) -> None:
        result = await _gql(client, "{ usersById(id: 99999) { id } }")
        assert result["data"]["usersById"] is None

    async def test_get_returns_all_columns(self, client: AsyncClient) -> None:
        created = (
            await _gql(
                client,
                'mutation { createUsers(input: { name: "Carol", email: "carol@x.com" }) { id } }',
            )
        )["data"]["createUsers"]
        result = await _gql(
            client,
            f'{{ usersById(id: {created["id"]}) {{ id name email score }} }}',
        )
        data = result["data"]["usersById"]
        assert all(k in data for k in ("id", "name", "email", "score"))


# ---------------------------------------------------------------------------
# Mutation – create
# ---------------------------------------------------------------------------


class TestCreateMutation:
    async def test_create_returns_row(self, client: AsyncClient) -> None:
        result = await _gql(
            client,
            'mutation { createUsers(input: { name: "Dave", email: "dave@x.com" }) { id name email } }',
        )
        assert result.get("errors") is None
        row = result["data"]["createUsers"]
        assert row["name"] == "Dave"
        assert "id" in row

    async def test_create_missing_required_field_returns_error(self, client: AsyncClient) -> None:
        result = await _gql(
            client,
            'mutation { createUsers(input: { name: "Eve" }) { id } }',
        )
        assert result.get("errors") is not None

    async def test_create_duplicate_unique_returns_error(self, client: AsyncClient) -> None:
        await _gql(
            client,
            'mutation { createUsers(input: { name: "Frank", email: "frank@x.com" }) { id } }',
        )
        result = await _gql(
            client,
            'mutation { createUsers(input: { name: "Frank2", email: "frank@x.com" }) { id } }',
        )
        assert result.get("errors") is not None

    async def test_create_duplicate_unique_returns_conflict_extension(
        self, client: AsyncClient
    ) -> None:
        """P0-4 / spec 05: a unique-constraint violation MUST surface as a
        ``GraphQLError`` with ``extensions.code = "CONFLICT"`` and the
        original message — NOT as a generic "Internal server error".

        Before the fix, ``_create_resolver`` re-raised ``IntegrityError`` as
        a bare ``Exception("Unique constraint violated")``. Strawberry's
        default masking turned that into ``Internal server error`` with no
        ``extensions.code``, so clients had no way to detect the conflict
        without parsing free-form text.
        """
        await _gql(
            client,
            'mutation { createUsers(input: { name: "Conflict1", email: "c@x.com" }) { id } }',
        )
        result = await _gql(
            client,
            'mutation { createUsers(input: { name: "Conflict2", email: "c@x.com" }) { id } }',
        )
        errors = result.get("errors") or []
        assert errors, "expected GraphQL errors on duplicate unique"
        first = errors[0]
        assert first.get("message") == "Unique constraint violated", (
            f"expected actionable error message, got {first.get('message')!r}"
        )
        ext = first.get("extensions") or {}
        assert ext.get("code") == "CONFLICT", (
            f"expected extensions.code='CONFLICT', got {ext!r}. "
            "Bare Exception masking lost the error code."
        )

    async def test_create_explicit_null_writes_null(self, client: AsyncClient) -> None:
        """P0-5: explicit null in create must write SQL NULL, not let the DEFAULT fire."""
        # users.score has DEFAULT 0.0. Sending score=null must write NULL, not 0.0.
        result = await _gql(
            client,
            'mutation { createUsers(input: { name: "ScoreNull", email: "sn@x.com", score: null }) { id score } }',
        )
        assert result.get("errors") is None
        assert result["data"]["createUsers"]["score"] is None


# ---------------------------------------------------------------------------
# Mutation – update
# ---------------------------------------------------------------------------


class TestUpdateMutation:
    async def test_update_returns_updated_row(self, client: AsyncClient) -> None:
        created = (
            await _gql(
                client,
                'mutation { createUsers(input: { name: "Grace", email: "grace@x.com" }) { id } }',
            )
        )["data"]["createUsers"]
        result = await _gql(
            client,
            f'mutation {{ updateUsers(id: {created["id"]}, input: {{ name: "Grace Updated" }}) {{ name }} }}',
        )
        assert result.get("errors") is None
        assert result["data"]["updateUsers"]["name"] == "Grace Updated"

    async def test_update_only_provided_fields(self, client: AsyncClient) -> None:
        created = (
            await _gql(
                client,
                'mutation { createUsers(input: { name: "Hank", email: "hank@x.com" }) { id } }',
            )
        )["data"]["createUsers"]
        await _gql(
            client,
            f'mutation {{ updateUsers(id: {created["id"]}, input: {{ name: "Hank New" }}) {{ id }} }}',
        )
        row = (
            await _gql(
                client,
                f'{{ usersById(id: {created["id"]}) {{ name email }} }}',
            )
        )["data"]["usersById"]
        assert row["name"] == "Hank New"
        assert row["email"] == "hank@x.com"

    async def test_update_nonexistent_returns_null(self, client: AsyncClient) -> None:
        result = await _gql(
            client,
            'mutation { updateUsers(id: 99999, input: { name: "Ghost" }) { id } }',
        )
        assert result.get("errors") is None
        assert result["data"]["updateUsers"] is None

    async def test_update_set_field_to_null(self, client: AsyncClient) -> None:
        """P0-3: update with explicit null must write SQL NULL, not skip the field."""
        # Create a post with a body, then null it out
        # First need a user for the FK
        user = (
            await _gql(
                client,
                'mutation { createUsers(input: { name: "NullTest", email: "nt@x.com" }) { id } }',
            )
        )["data"]["createUsers"]
        created = (
            await _gql(
                client,
                f'mutation {{ createPosts(input: {{ userId: {user["id"]}, title: "T", body: "original" }}) {{ id body }} }}',
            )
        )["data"]["createPosts"]
        assert created["body"] == "original"
        # Set body to null
        result = await _gql(
            client,
            f'mutation {{ updatePosts(id: {created["id"]}, input: {{ body: null }}) {{ id body }} }}',
        )
        assert result.get("errors") is None
        assert result["data"]["updatePosts"]["body"] is None


# ---------------------------------------------------------------------------
# Mutation – delete
# ---------------------------------------------------------------------------


class TestDeleteMutation:
    async def test_delete_returns_true(self, client: AsyncClient) -> None:
        created = (
            await _gql(
                client,
                'mutation { createUsers(input: { name: "Ivy", email: "ivy@x.com" }) { id } }',
            )
        )["data"]["createUsers"]
        result = await _gql(
            client,
            f'mutation {{ deleteUsers(id: {created["id"]}) }}',
        )
        assert result.get("errors") is None
        assert result["data"]["deleteUsers"] is True

    async def test_delete_removes_row(self, client: AsyncClient) -> None:
        created = (
            await _gql(
                client,
                'mutation { createUsers(input: { name: "Jack", email: "jack@x.com" }) { id } }',
            )
        )["data"]["createUsers"]
        await _gql(client, f'mutation {{ deleteUsers(id: {created["id"]}) }}')
        result = await _gql(client, f'{{ usersById(id: {created["id"]}) {{ id }} }}')
        assert result["data"]["usersById"] is None

    async def test_delete_nonexistent_returns_false(self, client: AsyncClient) -> None:
        result = await _gql(client, "mutation { deleteUsers(id: 99999) }")
        assert result.get("errors") is None
        assert result["data"]["deleteUsers"] is False


# ---------------------------------------------------------------------------
# No-PK table (audit_log)
# ---------------------------------------------------------------------------


class TestNoPkTable:
    async def test_no_pk_create(self, client: AsyncClient) -> None:
        result = await _gql(
            client,
            'mutation { createAuditLog(input: { message: "hello", level: "info" }) { message } }',
        )
        assert result.get("errors") is None
        assert result["data"]["createAuditLog"]["message"] == "hello"

    async def test_no_pk_list(self, client: AsyncClient) -> None:
        result = await _gql(client, "{ auditLog { edges { node { message } } } }")
        assert result.get("errors") is None
        assert isinstance(result["data"]["auditLog"]["edges"], list)

    def test_no_pk_no_byid_field(self, generator: GraphqlApiGenerator, tables: list[TableInfo]) -> None:
        schema = generator.generate(tables)
        schema_str = str(schema)
        assert "auditLogById" not in schema_str

    def test_no_pk_no_update_mutation(self, generator: GraphqlApiGenerator, tables: list[TableInfo]) -> None:
        schema = generator.generate(tables)
        schema_str = str(schema)
        assert "updateAuditLog" not in schema_str

    def test_no_pk_no_delete_mutation(self, generator: GraphqlApiGenerator, tables: list[TableInfo]) -> None:
        schema = generator.generate(tables)
        schema_str = str(schema)
        assert "deleteAuditLog" not in schema_str


# ---------------------------------------------------------------------------
# Composite PK table (order_items)
# ---------------------------------------------------------------------------


class TestCompositePkTable:
    async def test_composite_pk_create(self, client: AsyncClient) -> None:
        result = await _gql(
            client,
            "mutation { createOrderItems(input: { orderId: 1, itemId: 1, qty: 3 }) { orderId itemId qty } }",
        )
        assert result.get("errors") is None
        assert result["data"]["createOrderItems"]["qty"] == 3

    async def test_composite_pk_byid(self, client: AsyncClient) -> None:
        await _gql(
            client,
            "mutation { createOrderItems(input: { orderId: 2, itemId: 5 }) { orderId } }",
        )
        result = await _gql(
            client,
            "{ orderItemsById(orderId: 2, itemId: 5) { qty } }",
        )
        assert result.get("errors") is None
        assert result["data"]["orderItemsById"] is not None


# ---------------------------------------------------------------------------
# Cursor encoding (P0-5 / spec 05): non-JSON-native PK types must not crash.
# ---------------------------------------------------------------------------


class TestStringPkTable:
    """P0-9: tables with non-int PK must use the correct argument type in GraphQL."""

    async def test_create_and_byid_with_string_pk(self, client: AsyncClient) -> None:
        result = await _gql(
            client,
            'mutation { createTags(input: { slug: "python", label: "Python" }) { slug label } }',
        )
        assert result.get("errors") is None
        assert result["data"]["createTags"]["slug"] == "python"

        # Query by string PK
        result = await _gql(
            client,
            '{ tagsById(id: "python") { slug label } }',
        )
        assert result.get("errors") is None
        assert result["data"]["tagsById"]["label"] == "Python"


# ---------------------------------------------------------------------------


class TestCursorEncoding:
    """The encoder is opaque to clients — only round-trip stability matters.

    Before the fix, ``json.dumps({'id': datetime.now()})`` raised
    ``TypeError: Object of type datetime is not JSON serializable`` and any
    list query on a table keyed on ``datetime`` / ``UUID`` / ``Decimal``
    crashed with 500.
    """

    def test_encode_datetime_pk(self) -> None:
        from dbzap.generators.graphql import _decode_cursor, _encode_cursor

        ts = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
        token = _encode_cursor({"id": ts})
        assert isinstance(token, str)
        # Round-trip — decoded value is a string (cursors are opaque).
        decoded = _decode_cursor(token)
        assert "id" in decoded
        assert ts.isoformat() in str(decoded["id"])

    def test_encode_uuid_pk(self) -> None:
        from dbzap.generators.graphql import _decode_cursor, _encode_cursor

        u = uuid.UUID("12345678-1234-5678-1234-567812345678")
        token = _encode_cursor({"id": u})
        decoded = _decode_cursor(token)
        assert str(u) == decoded["id"]

    def test_encode_decimal_pk(self) -> None:
        from dbzap.generators.graphql import _decode_cursor, _encode_cursor

        d = decimal.Decimal("12345.6789")
        token = _encode_cursor({"id": d})
        decoded = _decode_cursor(token)
        assert str(d) == decoded["id"]

    def test_encode_date_pk(self) -> None:
        from dbzap.generators.graphql import _decode_cursor, _encode_cursor

        token = _encode_cursor({"id": datetime.date(2026, 6, 4)})
        decoded = _decode_cursor(token)
        assert "2026-06-04" in str(decoded["id"])

    def test_encode_bytes_pk(self) -> None:
        from dbzap.generators.graphql import _encode_cursor

        # Just must not raise — bytes aren't JSON-native.
        _encode_cursor({"id": b"\x00\x01\x02"})

    def test_encode_int_pk_unchanged(self) -> None:
        """Existing int-PK behavior must keep working."""
        from dbzap.generators.graphql import _decode_cursor, _encode_cursor

        token = _encode_cursor({"id": 42})
        decoded = _decode_cursor(token)
        assert decoded["id"] == 42
