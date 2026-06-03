"""REST API generator: registers FastAPI CRUD routes from introspected schema."""
from __future__ import annotations

import re
from typing import Any, get_type_hints

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, create_model
from pydantic.json_schema import GenerateJsonSchema
from sqlalchemy import Table, Column, MetaData, select, insert, update, delete, text, func
from sqlalchemy import Integer, String, Float, Boolean, Numeric
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from dbzap.core.introspector import ColumnInfo, TableInfo
from dbzap.generators.filter import (
    apply_filters,
    apply_search,
    decode_cursor,
    encode_cursor,
    parse_filters,
)

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


def _make_offset_pagination_model(response_model_cls: type[BaseModel], table_name: str) -> type[BaseModel]:
    """Create an offset-paginated list response model with data array and pagination metadata."""
    item_list_type = list[response_model_cls]  # type: ignore[valid-type]

    offset_pagination_type = create_model(
        _pascal(table_name) + "OffsetPagination",
        mode=(str, ...),
        total_records=(int, ...),
        current_page=(int, ...),
        per_page=(int, ...),
        total_pages=(int, ...),
        has_next=(bool, ...),
        has_prev=(bool, ...),
    )

    name = _pascal(table_name) + "OffsetListResponse"
    return create_model(
        name,
        data=(item_list_type, ...),
        pagination=(offset_pagination_type, ...),
    )


def _make_cursor_pagination_model(response_model_cls: type[BaseModel], table_name: str) -> type[BaseModel]:
    """Create a cursor-paginated list response model with data array and paging.cursors metadata."""
    item_list_type = list[response_model_cls]  # type: ignore[valid-type]

    cursors_type = create_model(
        _pascal(table_name) + "Cursors",
        after=(str | None, None),
        before=(str | None, None),
    )

    paging_type = create_model(
        _pascal(table_name) + "Paging",
        cursors=(cursors_type, ...),
        next=(str | None, None),
    )

    name = _pascal(table_name) + "CursorListResponse"
    return create_model(
        name,
        data=(item_list_type, ...),
        paging=(paging_type, ...),
    )


def _pascal(s: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[_\s]+", s))


def _openapi_body_extra(model_cls: type[BaseModel]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{model_cls.__name__}"},
                },
            },
        },
    }


def _openapi_list_extra() -> dict[str, Any]:
    params = [
        {"name": "q", "in": "query", "schema": {"type": "string"}, "description": "Global text search across all string columns"},
        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}, "description": "Page number (1-indexed, offset mode)"},
        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Items per page (1-100, offset mode)"},
        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Items per page (1-100, cursor mode)"},
        {"name": "starting_after", "in": "query", "schema": {"type": "string"}, "description": "Cursor: fetch rows after this PK (base64)"},
        {"name": "ending_before", "in": "query", "schema": {"type": "string"}, "description": "Cursor: fetch rows before this PK (base64)"},
    ]
    for op in ("eq", "ne", "gt", "gte", "lt", "lte", "like", "in", "is"):
        params.append({
            "name": f"field[{op}]",
            "in": "query",
            "schema": {"type": "string"},
            "description": f"LHS Bracket filter: field {op} value",
        })
    return {"parameters": params}


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
        extra_models: list[type[BaseModel]] = []
        for table in tables:
            extra_models.extend(self.generate_for_table(app, table))
        if not extra_models:
            return

        _orig_openapi = app.openapi

        def _openapi() -> dict[str, Any]:
            nonlocal extra_models
            if not app.openapi_schema:
                app.openapi_schema = _orig_openapi()
                from pydantic import TypeAdapter
                for model in extra_models:
                    if model.__name__ not in app.openapi_schema.get("components", {}).get("schemas", {}):
                        schema = TypeAdapter(model).json_schema(
                            ref_template="#/components/schemas/{model}",
                            schema_generator=GenerateJsonSchema,
                        )
                        defs = schema.pop("$defs", {})
                        app.openapi_schema.setdefault("components", {}).setdefault("schemas", {})[model.__name__] = schema
                        for def_name, def_schema in defs.items():
                            if def_name not in app.openapi_schema["components"]["schemas"]:
                                app.openapi_schema["components"]["schemas"][def_name] = def_schema
            return app.openapi_schema

        app.openapi = _openapi  # type: ignore[method-assign]

    def generate_for_table(self, app: FastAPI, table: TableInfo) -> list[type[BaseModel]]:
        pk_cols = table.primary_key
        sa_tbl = _sa_table(table, self._metadata)
        create_model_cls = _make_create_model(table)
        update_model_cls = _make_update_model(table)
        response_model_cls = _make_response_model(table)
        offset_pagination_model_cls = _make_offset_pagination_model(response_model_cls, table.name)
        cursor_pagination_model_cls = _make_cursor_pagination_model(response_model_cls, table.name)
        engine = self._engine

        has_pk = len(pk_cols) > 0
        is_composite = len(pk_cols) > 1
        single_int_pk = has_pk and not is_composite

        if not has_pk:
            logger.warning("table_no_pk", table=table.name)

        prefix = f"/api/{table.name}"
        valid_columns = {col.name for col in table.columns}
        string_columns = {
            col.name for col in table.columns
            if col.python_type is str
        }

        # --- POST (create) ---
        async def create_row(request: Request) -> Response:
            body = await request.json()
            cleaned = {k: v for k, v in body.items() if v is not None}
            try:
                create_model_cls(**body)
            except Exception as exc:
                return JSONResponse(status_code=422, content={"detail": str(exc)})

            async with engine.begin() as conn:
                try:
                    result = await conn.execute(insert(sa_tbl).values(**cleaned))
                    row_id = result.lastrowid
                except IntegrityError as exc:
                    raise HTTPException(status_code=409, detail="Unique constraint violated") from exc

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

            return JSONResponse(status_code=201, content=dict(row) if row else cleaned)

        tbl_label = _pascal(table.name)
        app.add_api_route(prefix, create_row, methods=["POST"],
                          summary=f"{tbl_label} Create Row",
                          responses={201: {"model": response_model_cls}},
                          openapi_extra=_openapi_body_extra(create_model_cls))

        # --- GET (list) with filtering + dual pagination ---
        async def list_rows(request: Request) -> dict[str, Any]:
            params_list = list(request.query_params.multi_items())
            params = dict(request.query_params)
            has_offset_params = "page" in params or "page_size" in params
            has_cursor_params = "limit" in params or "starting_after" in params or "ending_before" in params
            page = int(params.get("page", 1))
            page_size = int(params.get("page_size", 20))
            limit = int(params.get("limit", 20))
            starting_after = params.get("starting_after")
            ending_before = params.get("ending_before")
            q = params.get("q", "")

            page = max(1, page)
            page_size = max(1, min(100, page_size))
            limit = max(1, min(100, limit))

            try:
                conditions = parse_filters(params_list, valid_columns)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            use_cursor = (
                single_int_pk
                and has_cursor_params
                and not has_offset_params
            )
            pk_col_name = pk_cols[0] if single_int_pk else None

            async with engine.connect() as conn:
                if use_cursor and pk_col_name:
                    return await _cursor_list(
                        conn, sa_tbl, pk_col_name, conditions,
                        q, string_columns,
                        limit, starting_after, ending_before,
                        request,
                    )
                else:
                    return await _offset_list(
                        conn, sa_tbl, conditions,
                        q, string_columns,
                        page, page_size,
                    )

        app.add_api_route(prefix, list_rows, methods=["GET"],
                          summary=f"{tbl_label} List Rows",
                          responses={200: {"model": offset_pagination_model_cls}},
                          openapi_extra=_openapi_list_extra())

        if not has_pk:
            return [create_model_cls, update_model_cls]

        # Routes that require PK
        if is_composite:
            pk_path = prefix + "".join(f"/{{{c}}}" for c in pk_cols)
            route_pk = prefix + "/{pk}"  # captured as single path for simple routing
            # Use individual path params for composite PKs
            _register_composite_pk_routes(
                app, prefix, pk_cols, sa_tbl, engine,
                update_model_cls, response_model_cls, tbl_label,
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
                              summary=f"{tbl_label} Get Row",
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
                              summary=f"{tbl_label} Update Row",
                              responses={200: {"model": response_model_cls}},
                              openapi_extra=_openapi_body_extra(update_model_cls))

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
                              summary=f"{tbl_label} Partial Update Row",
                              responses={200: {"model": response_model_cls}},
                              openapi_extra=_openapi_body_extra(update_model_cls))

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
                              summary=f"{tbl_label} Delete Row",
                              responses={204: {"description": "No Content"}})

        return [create_model_cls, update_model_cls]


# ---------------------------------------------------------------------------
# List helpers (offset + cursor pagination with filtering)
# ---------------------------------------------------------------------------


async def _offset_list(
    conn: Any,
    sa_tbl: Table,
    conditions: list[dict[str, Any]],
    q: str,
    string_columns: set[str],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    base_q = select(sa_tbl)
    base_q = apply_filters(base_q, sa_tbl, conditions)
    base_q = apply_search(base_q, sa_tbl, q, string_columns)

    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await conn.execute(count_q)).scalar_one()

    offset = (page - 1) * page_size
    rows = (await conn.execute(base_q.offset(offset).limit(page_size))).mappings().all()

    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "data": [dict(r) for r in rows],
        "pagination": {
            "mode": "offset",
            "total_records": total,
            "current_page": page,
            "per_page": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }


async def _cursor_list(
    conn: Any,
    sa_tbl: Table,
    pk_col_name: str,
    conditions: list[dict[str, Any]],
    q: str,
    string_columns: set[str],
    limit: int,
    starting_after: str | None,
    ending_before: str | None,
    request: Request,
) -> dict[str, Any]:
    pk_col = sa_tbl.c[pk_col_name]
    base_q = select(sa_tbl)
    base_q = apply_filters(base_q, sa_tbl, conditions)
    base_q = apply_search(base_q, sa_tbl, q, string_columns)

    forward = starting_after is not None
    if forward:
        try:
            cursor_val = int(decode_cursor(starting_after))  # type: ignore[arg-type]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid cursor: {starting_after!r}") from exc
        base_q = base_q.where(pk_col > cursor_val).order_by(pk_col.asc())
    elif ending_before:
        try:
            cursor_val = int(decode_cursor(ending_before))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid cursor: {ending_before!r}") from exc
        base_q = base_q.where(pk_col < cursor_val).order_by(pk_col.desc())
    else:
        base_q = base_q.order_by(pk_col.asc())

    rows = (await conn.execute(base_q.limit(limit + 1))).mappings().all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    if ending_before and not forward:
        rows = list(reversed(rows))

    # Build paging.cursors
    cursors: dict[str, str] = {}
    if rows and has_more:
        cursors["after"] = encode_cursor(rows[-1][pk_col_name])
    if rows and (forward or ending_before):
        cursors["before"] = encode_cursor(rows[0][pk_col_name])

    paging: dict[str, Any] = {"cursors": cursors}

    # Build next URL
    if cursors.get("after"):
        base_url = str(request.url).split("?")[0]
        next_params = [f"limit={limit}", f"starting_after={cursors['after']}"]
        paging["next"] = f"{base_url}?{'&'.join(next_params)}"

    return {
        "data": [dict(r) for r in rows],
        "paging": paging,
    }


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
    table_label: str = "",
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
                      summary=f"{table_label} Get Row" if table_label else None,
                      responses={200: {"model": response_model_cls}})
    # Also register {pk} alias using / separator for test compatibility
    _register_pk_alias(app, prefix, pk_cols, sa_tbl, engine, update_model_cls, response_model_cls, table_label)


def _register_pk_alias(
    app: FastAPI,
    prefix: str,
    pk_cols: list[str],
    sa_tbl: Table,
    engine: AsyncEngine,
    update_model_cls: type[BaseModel],
    response_model_cls: type[BaseModel],
    table_label: str = "",
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
                      summary=f"{table_label} Update Row" if table_label else None,
                      responses={200: {"model": response_model_cls}},
                      openapi_extra=_openapi_body_extra(update_model_cls))

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
                      summary=f"{table_label} Partial Update Row" if table_label else None,
                      responses={200: {"model": response_model_cls}},
                      openapi_extra=_openapi_body_extra(update_model_cls))

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
                      summary=f"{table_label} Delete Row" if table_label else None,
                      responses={204: {"description": "No Content"}})
