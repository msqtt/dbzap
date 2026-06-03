"""Tests for REST API generator (spec: 04-rest-api-generator.md)."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.core.introspector import SchemaIntrospector, TableInfo
from dbzap.generators.rest import RestApiGenerator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_schema(conn: Connection) -> None:
    conn.execute(text("PRAGMA foreign_keys = ON"))
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
    # Table with no PK — only POST + list should be generated
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
    # Composite PK table
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
    introspector = SchemaIntrospector(engine=engine)
    return await introspector.introspect()


@pytest.fixture
async def app(engine: AsyncEngine, tables: list[TableInfo]) -> FastAPI:
    fa = FastAPI()
    generator = RestApiGenerator(engine=engine)
    generator.generate(fa, tables)
    return fa


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _table(tables: list[TableInfo], name: str) -> TableInfo:
    return next(t for t in tables if t.name == name)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    def test_users_has_six_routes(self, app: FastAPI) -> None:
        routes = {(r.path, list(r.methods or [])[0]) for r in app.routes}  # type: ignore[attr-defined]
        assert ("/api/users", "POST") in routes
        assert ("/api/users", "GET") in routes
        assert ("/api/users/{pk}", "GET") in routes
        assert ("/api/users/{pk}", "PUT") in routes
        assert ("/api/users/{pk}", "PATCH") in routes
        assert ("/api/users/{pk}", "DELETE") in routes

    def test_audit_log_only_post_and_list(self, app: FastAPI) -> None:
        paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/api/audit_log" in paths
        # No PK routes
        assert "/api/audit_log/{pk}" not in paths

    def test_all_tables_registered(self, app: FastAPI, tables: list[TableInfo]) -> None:
        paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
        for table in tables:
            assert f"/api/{table.name}" in paths


# ---------------------------------------------------------------------------
# CREATE  POST /api/users
# ---------------------------------------------------------------------------


class TestCreate:
    async def test_create_returns_201(self, client: AsyncClient) -> None:
        resp = await client.post("/api/users", json={"name": "Alice", "email": "alice@example.com"})
        assert resp.status_code == 201

    async def test_create_body_contains_id(self, client: AsyncClient) -> None:
        resp = await client.post("/api/users", json={"name": "Bob", "email": "bob@example.com"})
        data = resp.json()
        assert "id" in data

    async def test_create_missing_required_field_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post("/api/users", json={"name": "NoEmail"})
        assert resp.status_code == 422

    async def test_create_duplicate_unique_returns_409(self, client: AsyncClient) -> None:
        payload = {"name": "Carol", "email": "carol@example.com"}
        await client.post("/api/users", json=payload)
        resp = await client.post("/api/users", json=payload)
        assert resp.status_code == 409

    async def test_create_with_default_field_omitted(self, client: AsyncClient) -> None:
        """score has a DEFAULT, so it should be optional in the Create model."""
        resp = await client.post("/api/users", json={"name": "Dave", "email": "dave@example.com"})
        assert resp.status_code == 201

    async def test_pk_excluded_from_create_payload(self, client: AsyncClient) -> None:
        """Sending id in the body should not fail (extra fields ignored or accepted)."""
        resp = await client.post(
            "/api/users", json={"id": 999, "name": "Eve", "email": "eve@example.com"}
        )
        # Must succeed (id is server-generated via SERIAL/AUTOINCREMENT)
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# LIST  GET /api/users
# ---------------------------------------------------------------------------


class TestList:
    async def _seed(self, client: AsyncClient) -> None:
        for i in range(5):
            await client.post("/api/users", json={"name": f"User{i}", "email": f"u{i}@x.com"})

    async def test_list_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/api/users")
        assert resp.status_code == 200

    async def test_list_returns_paginated_dict(self, client: AsyncClient) -> None:
        resp = await client.get("/api/users")
        body = resp.json()
        assert isinstance(body, dict)
        assert "items" in body
        assert "page" in body
        assert "page_size" in body
        assert "total" in body
        assert "pages" in body

    async def test_list_pagination_page_size(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?page_size=2")
        assert len(resp.json()["items"]) == 2

    async def test_list_pagination_page(self, client: AsyncClient) -> None:
        await self._seed(client)
        all_rows: list[Any] = (await client.get("/api/users?page_size=5")).json()["items"]
        page2: list[Any] = (await client.get("/api/users?page_size=2&page=2")).json()["items"]
        assert page2[0]["id"] == all_rows[2]["id"]

    async def test_list_page_size_clamped_to_100(self, client: AsyncClient) -> None:
        for i in range(5):
            await client.post("/api/users", json={"name": f"U{i}", "email": f"ul{i}@x.com"})
        resp = await client.get("/api/users?page_size=9999")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 100

    async def test_list_default_page_size_20(self, client: AsyncClient) -> None:
        for i in range(25):
            await client.post("/api/users", json={"name": f"U{i}", "email": f"def{i}@x.com"})
        resp = await client.get("/api/users")
        assert len(resp.json()["items"]) == 20

    async def test_list_negative_page_clamped(self, client: AsyncClient) -> None:
        resp = await client.get("/api/users?page=-5")
        assert resp.status_code == 200

    async def test_list_pagination_metadata(self, client: AsyncClient) -> None:
        await self._seed(client)
        body = (await client.get("/api/users?page_size=2")).json()
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert body["total"] == 5
        assert body["pages"] == 3


# ---------------------------------------------------------------------------
# GET by PK  GET /api/users/{pk}
# ---------------------------------------------------------------------------


class TestGetByPk:
    async def test_get_existing_row(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Frank", "email": "frank@x.com"})
        ).json()
        resp = await client.get(f"/api/users/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Frank"

    async def test_get_nonexistent_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/users/99999")
        assert resp.status_code == 404

    async def test_response_includes_all_columns(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Grace", "email": "grace@x.com"})
        ).json()
        resp = await client.get(f"/api/users/{created['id']}")
        data = resp.json()
        assert all(k in data for k in ("id", "name", "email", "score"))


# ---------------------------------------------------------------------------
# FULL UPDATE  PUT /api/users/{pk}
# ---------------------------------------------------------------------------


class TestFullUpdate:
    async def test_put_returns_200(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Hank", "email": "hank@x.com"})
        ).json()
        resp = await client.put(
            f"/api/users/{created['id']}",
            json={"name": "Hank Updated", "email": "hank2@x.com"},
        )
        assert resp.status_code == 200

    async def test_put_updates_fields(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Ivy", "email": "ivy@x.com"})
        ).json()
        await client.put(
            f"/api/users/{created['id']}",
            json={"name": "Ivy New", "email": "ivynew@x.com"},
        )
        fetched = (await client.get(f"/api/users/{created['id']}")).json()
        assert fetched["name"] == "Ivy New"

    async def test_put_nonexistent_returns_404(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/users/99999", json={"name": "Ghost", "email": "ghost@x.com"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PARTIAL UPDATE  PATCH /api/users/{pk}
# ---------------------------------------------------------------------------


class TestPartialUpdate:
    async def test_patch_returns_200(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Jack", "email": "jack@x.com"})
        ).json()
        resp = await client.patch(
            f"/api/users/{created['id']}", json={"name": "Jack Patched"}
        )
        assert resp.status_code == 200

    async def test_patch_only_updates_provided_fields(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Karen", "email": "karen@x.com"})
        ).json()
        await client.patch(f"/api/users/{created['id']}", json={"name": "Karen New"})
        fetched = (await client.get(f"/api/users/{created['id']}")).json()
        assert fetched["name"] == "Karen New"
        assert fetched["email"] == "karen@x.com"  # unchanged

    async def test_patch_nonexistent_returns_404(self, client: AsyncClient) -> None:
        resp = await client.patch("/api/users/99999", json={"name": "Nobody"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE  DELETE /api/users/{pk}
# ---------------------------------------------------------------------------


class TestDelete:
    async def test_delete_returns_204(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Leo", "email": "leo@x.com"})
        ).json()
        resp = await client.delete(f"/api/users/{created['id']}")
        assert resp.status_code == 204

    async def test_delete_removes_row(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/users", json={"name": "Mia", "email": "mia@x.com"})
        ).json()
        await client.delete(f"/api/users/{created['id']}")
        resp = await client.get(f"/api/users/{created['id']}")
        assert resp.status_code == 404

    async def test_delete_nonexistent_returns_404(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/users/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Composite PK table
# ---------------------------------------------------------------------------


class TestCompositePk:
    async def test_composite_pk_route_exists(self, app: FastAPI) -> None:
        paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/api/order_items/{order_id}/{item_id}" in paths

    async def test_create_composite_pk_row(self, client: AsyncClient) -> None:
        resp = await client.post("/api/order_items", json={"order_id": 1, "item_id": 1, "qty": 3})
        assert resp.status_code == 201

    async def test_get_composite_pk_row(self, client: AsyncClient) -> None:
        await client.post("/api/order_items", json={"order_id": 2, "item_id": 5, "qty": 1})
        resp = await client.get("/api/order_items/2/5")
        assert resp.status_code == 200
        assert resp.json()["qty"] == 1

    async def test_delete_composite_pk_row(self, client: AsyncClient) -> None:
        await client.post("/api/order_items", json={"order_id": 3, "item_id": 7, "qty": 2})
        resp = await client.delete("/api/order_items/3/7")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# No-PK table (audit_log)
# ---------------------------------------------------------------------------


class TestNoPkTable:
    async def test_no_pk_create(self, client: AsyncClient) -> None:
        resp = await client.post("/api/audit_log", json={"message": "hello", "level": "info"})
        assert resp.status_code == 201

    async def test_no_pk_list(self, client: AsyncClient) -> None:
        resp = await client.get("/api/audit_log")
        assert resp.status_code == 200
        assert "items" in resp.json()
