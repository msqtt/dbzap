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
        routes = {(r.path, next(iter(r.methods or []))) for r in app.routes}  # type: ignore[attr-defined]
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
        assert "data" in body
        assert "pagination" in body
        pg = body["pagination"]
        assert "mode" in pg
        assert "total_records" in pg
        assert "current_page" in pg
        assert "per_page" in pg
        assert "total_pages" in pg
        assert "has_next" in pg
        assert "has_prev" in pg

    async def test_list_pagination_page_size(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?page_size=2")
        assert len(resp.json()["data"]) == 2

    async def test_list_pagination_page(self, client: AsyncClient) -> None:
        await self._seed(client)
        all_rows: list[Any] = (await client.get("/api/users?page_size=5")).json()["data"]
        page2: list[Any] = (await client.get("/api/users?page_size=2&page=2")).json()["data"]
        assert page2[0]["id"] == all_rows[2]["id"]

    async def test_list_page_size_clamped_to_100(self, client: AsyncClient) -> None:
        for i in range(5):
            await client.post("/api/users", json={"name": f"U{i}", "email": f"ul{i}@x.com"})
        resp = await client.get("/api/users?page_size=9999")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) <= 100

    async def test_list_default_page_size_20(self, client: AsyncClient) -> None:
        for i in range(25):
            await client.post("/api/users", json={"name": f"U{i}", "email": f"def{i}@x.com"})
        resp = await client.get("/api/users")
        assert len(resp.json()["data"]) == 20

    async def test_list_negative_page_clamped(self, client: AsyncClient) -> None:
        resp = await client.get("/api/users?page=-5")
        assert resp.status_code == 200

    async def test_list_pagination_metadata(self, client: AsyncClient) -> None:
        await self._seed(client)
        body = (await client.get("/api/users?page_size=2")).json()
        pg = body["pagination"]
        assert pg["current_page"] == 1
        assert pg["per_page"] == 2
        assert pg["total_records"] == 5
        assert pg["total_pages"] == 3


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
        assert "data" in resp.json()


# ---------------------------------------------------------------------------
# Filtering  GET /api/users?field[op]=value
# ---------------------------------------------------------------------------


class TestFiltering:
    async def _seed(self, client: AsyncClient) -> None:
        await client.post("/api/users", json={"name": "Alice", "email": "alice@x.com", "score": 95.0})
        await client.post("/api/users", json={"name": "Bob", "email": "bob@x.com", "score": 80.0})
        await client.post("/api/users", json={"name": "Carol", "email": "carol@x.com", "score": 65.0})
        await client.post("/api/users", json={"name": "Dave", "email": "dave@x.com", "score": 50.0})

    async def test_eq_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?name=Alice")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Alice"

    async def test_eq_filter_bracket(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?name[eq]=Bob")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Bob"

    async def test_ne_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?name[ne]=Alice")
        data = resp.json()["data"]
        assert len(data) == 3

    async def test_gt_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?score[gt]=80")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Alice"

    async def test_gte_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?score[gte]=80")
        data = resp.json()["data"]
        assert len(data) == 2

    async def test_lt_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?score[lt]=65")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Dave"

    async def test_lte_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?score[lte]=65")
        data = resp.json()["data"]
        assert len(data) == 2

    async def test_like_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?name[like]=li")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Alice"

    async def test_in_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?name[in]=Alice,Carol")
        data = resp.json()["data"]
        assert len(data) == 2
        names = {r["name"] for r in data}
        assert names == {"Alice", "Carol"}

    async def test_and_multiple_filters(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?score[gte]=65&score[lte]=80")
        data = resp.json()["data"]
        assert len(data) == 2
        names = {r["name"] for r in data}
        assert names == {"Bob", "Carol"}

    async def test_filter_nonexistent_field_ignored(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?nonexistent=value")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 4

    async def test_filter_invalid_operator_returns_400(self, client: AsyncClient) -> None:
        resp = await client.get("/api/users?name[badop]=value")
        assert resp.status_code == 400

    async def test_filter_combined_with_pagination(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?score[gte]=65&page_size=1")
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["pagination"]["total_records"] == 3


# ---------------------------------------------------------------------------
# Search  GET /api/users?q=...
# ---------------------------------------------------------------------------


class TestSearch:
    async def _seed(self, client: AsyncClient) -> None:
        await client.post("/api/users", json={"name": "Alice", "email": "alice@x.com", "score": 95.0})
        await client.post("/api/users", json={"name": "Bob", "email": "bob@x.com", "score": 80.0})
        await client.post("/api/users", json={"name": "Carol", "email": "carol@example.com", "score": 65.0})

    async def test_q_matches_name(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=Alice")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Alice"

    async def test_q_matches_email(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=example")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Carol"

    async def test_q_matches_any_string_column(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=ob")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Bob"

    async def test_q_no_match(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=zzz")
        data = resp.json()["data"]
        assert len(data) == 0

    async def test_q_combined_with_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=alice&score[gte]=90")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Alice"

    async def test_q_combined_with_filter_no_match(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=alice&score[lte]=50")
        data = resp.json()["data"]
        assert len(data) == 0

    async def test_q_case_insensitive_like(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=ALICE")
        data = resp.json()["data"]
        assert len(data) >= 1

    async def test_q_empty_value(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?q=")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 3


# ---------------------------------------------------------------------------
# Cursor pagination  GET /api/users?starting_after=...
# ---------------------------------------------------------------------------


class TestCursorPagination:
    async def _seed(self, client: AsyncClient) -> None:
        for i in range(10):
            await client.post("/api/users", json={"name": f"User{i}", "email": f"cu{i}@x.com"})

    @staticmethod
    def _encode(pk: int) -> str:
        import base64
        return base64.urlsafe_b64encode(str(pk).encode()).decode()

    async def test_cursor_limit_only(self, client: AsyncClient) -> None:
        """Sending just limit (no offset params) activates cursor mode."""
        await self._seed(client)
        resp = await client.get("/api/users?limit=3")
        body = resp.json()
        assert "paging" in body
        assert len(body["data"]) == 3
        assert body["paging"]["cursors"]["after"] is not None

    async def test_cursor_first_page(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get(f"/api/users?limit=3&starting_after={self._encode(0)}")
        body = resp.json()
        assert "paging" in body
        assert len(body["data"]) == 3

    async def test_cursor_has_next(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get(f"/api/users?limit=3&starting_after={self._encode(0)}")
        paging = resp.json()["paging"]
        assert paging["cursors"]["after"] is not None
        assert "next" in paging

    async def test_cursor_chain(self, client: AsyncClient) -> None:
        await self._seed(client)
        all_ids: list[int] = []
        # First page: just limit
        resp = await client.get("/api/users?limit=3")
        body = resp.json()
        all_ids.extend(r["id"] for r in body["data"])
        # Subsequent pages: follow paging.next URL or use cursors.after
        for _ in range(4):
            after = body["paging"]["cursors"].get("after")
            if not after:
                break
            resp = await client.get(f"/api/users?limit=3&starting_after={after}")
            body = resp.json()
            all_ids.extend(r["id"] for r in body["data"])
        assert len(all_ids) == 10
        assert len(set(all_ids)) == 10

    async def test_cursor_no_duplicates(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp1 = await client.get(f"/api/users?limit=5&starting_after={self._encode(0)}")
        after = resp1.json()["paging"]["cursors"]["after"]
        resp2 = await client.get(f"/api/users?limit=5&starting_after={after}")
        ids1 = {r["id"] for r in resp1.json()["data"]}
        ids2 = {r["id"] for r in resp2.json()["data"]}
        assert ids1.isdisjoint(ids2)

    async def test_cursor_last_page_no_next(self, client: AsyncClient) -> None:
        await self._seed(client)
        all_resp = await client.get("/api/users?page_size=100")
        last_id = all_resp.json()["data"][-1]["id"]
        resp = await client.get(f"/api/users?limit=5&starting_after={self._encode(last_id)}")
        body = resp.json()
        assert len(body["data"]) == 0
        assert "after" not in body["paging"]["cursors"]
        assert "next" not in body["paging"]

    async def test_cursor_invalid_returns_400(self, client: AsyncClient) -> None:
        resp = await client.get("/api/users?starting_after=not-valid-base64!!!")
        assert resp.status_code == 400

    async def test_cursor_with_filter(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get(f"/api/users?limit=10&starting_after={self._encode(0)}&name[like]=User1")
        data = resp.json()["data"]
        for row in data:
            assert "User1" in row["name"]

    async def test_cursor_ending_before(self, client: AsyncClient) -> None:
        await self._seed(client)
        all_resp = await client.get("/api/users?page_size=100")
        last_id = all_resp.json()["data"][-1]["id"]
        resp = await client.get(f"/api/users?limit=3&ending_before={self._encode(last_id)}")
        body = resp.json()
        assert "paging" in body
        assert len(body["data"]) == 3
        for row in body["data"]:
            assert row["id"] < last_id

    async def test_cursor_paging_next_url(self, client: AsyncClient) -> None:
        await self._seed(client)
        resp = await client.get("/api/users?limit=3")
        paging = resp.json()["paging"]
        assert "next" in paging
        assert "starting_after=" in paging["next"]
        assert "limit=3" in paging["next"]

    async def test_offset_params_override_cursor(self, client: AsyncClient) -> None:
        """page/page_size takes precedence over limit."""
        await self._seed(client)
        resp = await client.get("/api/users?limit=3&page=1&page_size=5")
        body = resp.json()
        assert "pagination" in body
        assert body["pagination"]["mode"] == "offset"
        assert len(body["data"]) == 5
