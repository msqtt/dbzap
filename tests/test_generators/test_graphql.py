"""Tests for GraphQL API generator (spec: 05-graphql-api-generator.md)."""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Query – list
# ---------------------------------------------------------------------------


class TestListQuery:
    async def test_list_users_returns_empty_initially(self, client: AsyncClient) -> None:
        result = await _gql(client, "{ users { items { id name email } } }")
        assert result.get("errors") is None
        assert result["data"]["users"]["items"] == []

    async def test_list_users_returns_inserted_rows(self, client: AsyncClient) -> None:
        await _gql(
            client,
            "mutation { createUsers(input: { name: \"Alice\", email: \"a@x.com\" }) { id } }",
        )
        result = await _gql(client, "{ users { items { name } } }")
        assert result["data"]["users"]["items"][0]["name"] == "Alice"

    async def test_list_pagination_page_size(self, client: AsyncClient) -> None:
        for i in range(5):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "U{i}", email: "u{i}@x.com" }}) {{ id }} }}',
            )
        result = await _gql(client, "{ users(pageSize: 2) { items { id } } }")
        assert len(result["data"]["users"]["items"]) == 2

    async def test_list_pagination_page(self, client: AsyncClient) -> None:
        for i in range(4):
            await _gql(
                client,
                f'mutation {{ createUsers(input: {{ name: "U{i}", email: "off{i}@x.com" }}) {{ id }} }}',
            )
        all_rows = (await _gql(client, "{ users(pageSize: 4) { items { id } } }"))["data"]["users"]["items"]
        page2 = (await _gql(client, "{ users(pageSize: 2, page: 2) { items { id } } }"))["data"]["users"]["items"]
        assert page2[0]["id"] == all_rows[2]["id"]

    async def test_list_page_size_clamped_to_100(self, client: AsyncClient) -> None:
        result = await _gql(client, "{ users(pageSize: 9999) { items { id } } }")
        assert result.get("errors") is None


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
        result = await _gql(client, "{ auditLog { items { message } } }")
        assert result.get("errors") is None
        assert isinstance(result["data"]["auditLog"]["items"], list)

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
