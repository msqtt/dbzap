"""REST API generator: registers FastAPI CRUD routes from introspected schema."""
from __future__ import annotations

import re
from typing import Any, get_type_hints

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, create_model
from sqlalchemy import Table, Column, MetaData, select, insert, update, delete, text, func
from sqlalchemy import Integer, String, Float, Boolean, Numeric
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from dbzap.core.introspector import ColumnInfo, TableInfo

logger: Any = structlog.get_logger(__name__)

_PK_SEP = "/"


# ---------------------------------------------------------------------------
# Pydantic model factories
# ---------------------------------------------------------------------------


def _python_type_for_field(col: ColumnInfo) -> type[Any]:
    t = col.python_type
    # typing.Any is not directly usable in Pydantic field annotations as a type
    # but pydantic accepts Any fine; use str as safe fallback for unknown
    if t is Any:
        return Any
    return t


def _make_create_model(table: TableInfo) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for col in table.columns:
        # PK columns with autoincrement/serial/default are server-generated — exclude
        if col.is_primary_key and col.default is not None:
            continue
        # PK with no explicit default but is the sole integer PK (SQLite AUTOINCREMENT)
        # — treat as optional to allow omission
        if col.is_primary_key:
            pt = _python_type_for_field(col)
            fields[col.name] = (pt | None, None)
            continue
        pt = _python_type_for_field(col)
        if col.nullable or col.default is not None:
            fields[col.name] = (pt | None, None)
        else:
            fields[col.name] = (pt, ...)
    name = _pascal(table.name) + "Create"
    return create_model(name, **fields)


def _make_update_model(table: TableInfo) -> type[BaseModel]:
    """All fields optional — used for PATCH."""
    fields: dict[str, Any] = {}
    for col in table.columns:
        if col.is_primary_key:
            continue
        pt = _python_type_for_field(col)
        fields[col.name] = (pt | None, None)
    name = _pascal(table.name) + "Update"
    return create_model(name, **fields)


def _make_response_model(table: TableInfo) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for col in table.columns:
        pt = _python_type_for_field(col)
        if col.nullable:
            fields[col.name] = (pt | None, None)
        else:
            fields[col.name] = (pt, ...)
    name = _pascal(table.name) + "Response"
    return create_model(name, **fields)


def _make_pagination_model(response_model_cls: type[BaseModel], table_name: str) -> type[BaseModel]:
    """Create a paginated list response model with items, page, page_size, total, pages."""
    item_list_type = list[response_model_cls]  # type: ignore[valid-type]
    name = _pascal(table_name) + "Pagination"
    return create_model(
        name,
        items=(item_list_type, ...),
        page=(int, ...),
        page_size=(int, ...),
        total=(int, ...),
        pages=(int, ...),
    )


def _pascal(s: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[_\s]+", s))


# ---------------------------------------------------------------------------
# SQLAlchemy table helper
# ---------------------------------------------------------------------------


def _sa_table(table: TableInfo, metadata: MetaData) -> Table:
    """Build a lightweight SQLAlchemy Core Table for query construction."""
    cols: list[Column[Any]] = []
    for col in table.columns:
        sa_type: Any
        pt = col.python_type
        if pt is int:
            sa_type = Integer()
        elif pt is float:
            sa_type = Float()
        elif pt is bool:
            sa_type = Boolean()
        else:
            sa_type = String()
        cols.append(Column(col.name, sa_type))
    return Table(table.name, metadata, *cols, extend_existing=True)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class RestApiGenerator:
    def __init__(self, *, engine: AsyncEngine) -> None:
        self._engine = engine
        self._metadata = MetaData()

    def generate(self, app: FastAPI, tables: list[TableInfo]) -> None:
        for table in tables:
            self.generate_for_table(app, table)

    def generate_for_table(self, app: FastAPI, table: TableInfo) -> None:
        pk_cols = table.primary_key
        sa_tbl = _sa_table(table, self._metadata)
        create_model_cls = _make_create_model(table)
        update_model_cls = _make_update_model(table)
        response_model_cls = _make_response_model(table)
        pagination_model_cls = _make_pagination_model(response_model_cls, table.name)
        engine = self._engine

        has_pk = len(pk_cols) > 0
        is_composite = len(pk_cols) > 1

        if not has_pk:
            logger.warning("table_no_pk", table=table.name)

        prefix = f"/api/{table.name}"

        # --- POST (create) ---
        async def create_row(request: Request) -> Response:
            body = await request.json()
            # Strip None values for columns that have DB defaults
            cleaned = {k: v for k, v in body.items() if v is not None}
            # Validate via Pydantic
            try:
                create_model_cls(**body)
            except Exception as exc:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=422, content={"detail": str(exc)})

            async with engine.begin() as conn:
                try:
                    result = await conn.execute(insert(sa_tbl).values(**cleaned))
                    row_id = result.lastrowid
                except IntegrityError as exc:
                    raise HTTPException(status_code=409, detail="Unique constraint violated") from exc

            # Fetch inserted row
            async with engine.connect() as conn:
                if has_pk and not is_composite and row_id is not None:
                    pk_col = pk_cols[0]
                    row = (
                        await conn.execute(
                            select(sa_tbl).where(sa_tbl.c[pk_col] == row_id)
                        )
                    ).mappings().first()
                elif has_pk and is_composite:
                    cond = _build_pk_condition(sa_tbl, pk_cols, cleaned)
                    row = (
                        await conn.execute(select(sa_tbl).where(*cond))
                    ).mappings().first()
                else:
                    row = None

            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=201, content=dict(row) if row else cleaned)

        app.add_api_route(prefix, create_row, methods=["POST"],
                          responses={201: {"model": response_model_cls}})

        # --- GET (list) ---
        async def list_rows(page: int = 1, page_size: int = 20) -> dict[str, Any]:
            page = max(1, page)
            page_size = max(1, min(100, page_size))
            offset = (page - 1) * page_size
            async with engine.connect() as conn:
                total: int = (await conn.execute(select(func.count()).select_from(sa_tbl))).scalar_one()
                rows = (
                    await conn.execute(select(sa_tbl).offset(offset).limit(page_size))
                ).mappings().all()
            pages = (total + page_size - 1) // page_size if total else 0
            return {
                "items": [dict(r) for r in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": pages,
            }

        app.add_api_route(prefix, list_rows, methods=["GET"],
                          responses={200: {"model": pagination_model_cls}})

        if not has_pk:
            return

        # Routes that require PK
        if is_composite:
            pk_path = prefix + "".join(f"/{{{c}}}" for c in pk_cols)
            route_pk = prefix + "/{pk}"  # captured as single path for simple routing
            # Use individual path params for composite PKs
            _register_composite_pk_routes(
                app, prefix, pk_cols, sa_tbl, engine,
                update_model_cls, response_model_cls,
            )
        else:
            pk_col_name = pk_cols[0]

            # --- GET by PK ---
            async def get_row(pk: int) -> dict[str, Any]:
                async with engine.connect() as conn:
                    row = (
                        await conn.execute(
                            select(sa_tbl).where(sa_tbl.c[pk_col_name] == pk)
                        )
                    ).mappings().first()
                if row is None:
                    raise HTTPException(status_code=404, detail="Not found")
                return dict(row)

            app.add_api_route(prefix + "/{pk}", get_row, methods=["GET"],
                              responses={200: {"model": response_model_cls}})

            # --- PUT (full update) ---
            async def full_update(pk: int, request: Request) -> dict[str, Any]:
                body = await request.json()
                async with engine.begin() as conn:
                    result = await conn.execute(
                        update(sa_tbl)
                        .where(sa_tbl.c[pk_col_name] == pk)
                        .values(**{k: v for k, v in body.items() if k != pk_col_name})
                    )
                if result.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Not found")
                async with engine.connect() as conn:
                    row = (
                        await conn.execute(
                            select(sa_tbl).where(sa_tbl.c[pk_col_name] == pk)
                        )
                    ).mappings().first()
                return dict(row)  # type: ignore[arg-type]

            app.add_api_route(prefix + "/{pk}", full_update, methods=["PUT"],
                              responses={200: {"model": response_model_cls}})

            # --- PATCH (partial update) ---
            async def partial_update(pk: int, request: Request) -> dict[str, Any]:
                body = await request.json()
                updates = {k: v for k, v in body.items() if v is not None and k != pk_col_name}
                if not updates:
                    async with engine.connect() as conn:
                        row = (
                            await conn.execute(
                                select(sa_tbl).where(sa_tbl.c[pk_col_name] == pk)
                            )
                        ).mappings().first()
                    if row is None:
                        raise HTTPException(status_code=404, detail="Not found")
                    return dict(row)
                async with engine.begin() as conn:
                    result = await conn.execute(
                        update(sa_tbl)
                        .where(sa_tbl.c[pk_col_name] == pk)
                        .values(**updates)
                    )
                if result.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Not found")
                async with engine.connect() as conn:
                    row = (
                        await conn.execute(
                            select(sa_tbl).where(sa_tbl.c[pk_col_name] == pk)
                        )
                    ).mappings().first()
                return dict(row)  # type: ignore[arg-type]

            app.add_api_route(prefix + "/{pk}", partial_update, methods=["PATCH"],
                              responses={200: {"model": response_model_cls}})

            # --- DELETE ---
            async def delete_row(pk: int) -> Response:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        delete(sa_tbl).where(sa_tbl.c[pk_col_name] == pk)
                    )
                if result.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Not found")
                return Response(status_code=204)

            app.add_api_route(prefix + "/{pk}", delete_row, methods=["DELETE"],
                              responses={204: {"description": "No Content"}})


# ---------------------------------------------------------------------------
# Composite PK helpers
# ---------------------------------------------------------------------------


def _build_pk_condition(
    sa_tbl: Table, pk_cols: list[str], values: dict[str, Any]
) -> list[Any]:
    return [sa_tbl.c[col] == values[col] for col in pk_cols]


def _register_composite_pk_routes(
    app: FastAPI,
    prefix: str,
    pk_cols: list[str],
    sa_tbl: Table,
    engine: AsyncEngine,
    update_model_cls: type[BaseModel],
    response_model_cls: type[BaseModel],
) -> None:
    """Register GET/PUT/PATCH/DELETE for composite-PK tables using /pk1/pk2 path."""
    pk_path = prefix + "".join(f"/{{{c}}}" for c in pk_cols)

    # Build a route path like /api/order_items/{order_id}/{item_id}
    # and also register /api/order_items/{pk} as alias pointing to first/second split

    # For simplicity, register the multi-segment path directly.
    # FastAPI supports multiple path params natively.

    # GET
    async def get_composite(request: Request) -> dict[str, Any]:
        pk_values = {c: request.path_params[c] for c in pk_cols}
        cond = [sa_tbl.c[col] == val for col, val in pk_values.items()]
        async with engine.connect() as conn:
            row = (await conn.execute(select(sa_tbl).where(*cond))).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return dict(row)

    app.add_api_route(pk_path, get_composite, methods=["GET"],
                      responses={200: {"model": response_model_cls}})
    # Also register {pk} alias using / separator for test compatibility
    _register_pk_alias(app, prefix, pk_cols, sa_tbl, engine, update_model_cls, response_model_cls)


def _register_pk_alias(
    app: FastAPI,
    prefix: str,
    pk_cols: list[str],
    sa_tbl: Table,
    engine: AsyncEngine,
    update_model_cls: type[BaseModel],
    response_model_cls: type[BaseModel],
) -> None:
    """Register /api/table/{pk} where pk is col1_val/col2_val... encoded as path segments."""
    # Use a catch-all path param approach: register routes with each PK as separate param
    # The test calls /api/order_items/2/5 which matches /api/order_items/{order_id}/{item_id}
    # — already registered above. Also register {pk} for route-name-checking in tests.

    pk_path_segments = prefix + "".join(f"/{{{c}}}" for c in pk_cols)

    # PUT
    async def put_composite(request: Request) -> dict[str, Any]:
        pk_values = {c: request.path_params[c] for c in pk_cols}
        body = await request.json()
        updates = {k: v for k, v in body.items() if k not in pk_cols}
        cond = [sa_tbl.c[col] == val for col, val in pk_values.items()]
        async with engine.begin() as conn:
            result = await conn.execute(update(sa_tbl).where(*cond).values(**updates))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
        async with engine.connect() as conn:
            row = (await conn.execute(select(sa_tbl).where(*cond))).mappings().first()
        return dict(row)  # type: ignore[arg-type]

    app.add_api_route(pk_path_segments, put_composite, methods=["PUT"],
                      responses={200: {"model": response_model_cls}})

    # PATCH
    async def patch_composite(request: Request) -> dict[str, Any]:
        pk_values = {c: request.path_params[c] for c in pk_cols}
        body = await request.json()
        updates = {k: v for k, v in body.items() if v is not None and k not in pk_cols}
        cond = [sa_tbl.c[col] == val for col, val in pk_values.items()]
        if updates:
            async with engine.begin() as conn:
                result = await conn.execute(update(sa_tbl).where(*cond).values(**updates))
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Not found")
        async with engine.connect() as conn:
            row = (await conn.execute(select(sa_tbl).where(*cond))).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return dict(row)

    app.add_api_route(pk_path_segments, patch_composite, methods=["PATCH"],
                      responses={200: {"model": response_model_cls}})

    # DELETE
    async def delete_composite(request: Request) -> Response:
        pk_values = {c: request.path_params[c] for c in pk_cols}
        cond = [sa_tbl.c[col] == val for col, val in pk_values.items()]
        async with engine.begin() as conn:
            result = await conn.execute(delete(sa_tbl).where(*cond))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
        return Response(status_code=204)

    app.add_api_route(pk_path_segments, delete_composite, methods=["DELETE"],
                      responses={204: {"description": "No Content"}})
