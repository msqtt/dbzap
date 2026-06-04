"""GraphQL API generator: builds a Strawberry schema with CRUD for every table."""
from __future__ import annotations

import base64
import dataclasses
import datetime
import decimal
import re
import sys
import types
import uuid
from typing import Any, Optional, cast, get_args

import orjson
import strawberry
import structlog
from fastapi import FastAPI
from graphql import GraphQLError  # noqa: F401

# NOTE: many of these imports look unused statically, but they are
# referenced *by name* inside the resolver source strings compiled via
# ``exec()`` (see ``_build_resolver``). Removing them breaks runtime.
from sqlalchemy import (  # noqa: F401
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncEngine
from strawberry.fastapi import GraphQLRouter

from dbzap.core.introspector import TableInfo

logger: Any = structlog.get_logger(__name__)

_MOD = __name__

# ---------------------------------------------------------------------------
# Type mapping: Python -> GraphQL
# ---------------------------------------------------------------------------

_GRAPHQL_TYPE_MAP: dict[type[Any], type[Any]] = {
    int: int,
    float: float,
    bool: bool,
    str: str,
    bytes: str,
    datetime.datetime: datetime.datetime,
    datetime.date: datetime.date,
    datetime.time: datetime.time,
    decimal.Decimal: decimal.Decimal,
    uuid.UUID: uuid.UUID,
    dict: str,
    list: str,
}

_RESERVED_GQL_NAMES = {"query", "mutation", "subscription", "schema", "type", "input", "enum"}


def _gql_type(python_type: type[Any]) -> type[Any]:
    return _GRAPHQL_TYPE_MAP.get(python_type, str)


def _pascal(s: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[_\s]+", s))


def _safe_name(table_name: str) -> str:
    if table_name.lower() in _RESERVED_GQL_NAMES:
        return f"Tbl_{table_name}"
    return table_name


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------


def _cursor_default(o: Any) -> str:
    """JSON encoder hook for non-JSON-native PK types.

    Cursors are opaque tokens — clients only echo them back as ``after`` /
    ``before`` arguments — so stringifying once is sufficient. ``datetime``
    / ``date`` / ``time`` use ``isoformat()`` for stability; everything
    else falls back to ``str(o)``. ``bytes`` is decoded as latin-1 to
    survive arbitrary binary without padding/escaping. See P0-5 / spec 05
    edge case "Cursor encoding".
    """
    if isinstance(o, datetime.datetime | datetime.date | datetime.time):
        return o.isoformat()
    if isinstance(o, uuid.UUID | decimal.Decimal):
        return str(o)
    if isinstance(o, bytes):
        return o.decode("latin-1")
    return str(o)


def _serialize_row(row_dict: dict[str, Any], json_columns: frozenset[str]) -> dict[str, Any]:
    """Convert dict/list column values to JSON strings for GraphQL output (P1-13)."""
    if not json_columns:
        return row_dict
    for col in json_columns:
        v = row_dict.get(col)
        if isinstance(v, (dict, list)):
            row_dict[col] = orjson.dumps(v).decode()
    return row_dict


def _encode_cursor(pk_values: dict[str, Any]) -> str:
    # P3-28: orjson natively handles datetime, UUID, Decimal — no custom
    # default needed for these types. Still use _cursor_default for edge cases.
    return base64.urlsafe_b64encode(
        orjson.dumps(pk_values, default=_cursor_default)
    ).decode()


def _decode_cursor(token: str) -> dict[str, Any]:
    try:
        return cast("dict[str, Any]", orjson.loads(base64.urlsafe_b64decode(token.encode())))
    except Exception as exc:
        raise ValueError(f"Invalid cursor: {token!r}") from exc


# ---------------------------------------------------------------------------
# Shared filter input types
# ---------------------------------------------------------------------------


def _make_int_filter(ns: dict[str, Any]) -> type[Any]:
    name = "IntFilter"
    if name in ns:
        return cast("type[Any]", ns[name])
    fields = [
        ("eq", Optional[int], dataclasses.field(default=None)),
        ("gt", Optional[int], dataclasses.field(default=None)),
        ("lt", Optional[int], dataclasses.field(default=None)),
        ("gte", Optional[int], dataclasses.field(default=None)),
        ("lte", Optional[int], dataclasses.field(default=None)),
    ]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _make_float_filter(ns: dict[str, Any]) -> type[Any]:
    name = "FloatFilter"
    if name in ns:
        return cast("type[Any]", ns[name])
    fields = [
        ("eq", Optional[float], dataclasses.field(default=None)),
        ("gt", Optional[float], dataclasses.field(default=None)),
        ("lt", Optional[float], dataclasses.field(default=None)),
        ("gte", Optional[float], dataclasses.field(default=None)),
        ("lte", Optional[float], dataclasses.field(default=None)),
    ]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _make_string_filter(ns: dict[str, Any]) -> type[Any]:
    name = "StringFilter"
    if name in ns:
        return cast("type[Any]", ns[name])
    fields = [
        ("eq", Optional[str], dataclasses.field(default=None)),
        ("contains", Optional[str], dataclasses.field(default=None)),
        ("startsWith", Optional[str], dataclasses.field(default=None)),
    ]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _make_boolean_filter(ns: dict[str, Any]) -> type[Any]:
    name = "BooleanFilter"
    if name in ns:
        return cast("type[Any]", ns[name])
    fields = [
        ("eq", Optional[bool], dataclasses.field(default=None)),
    ]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _filter_type_for_python_type(pt: type[Any], ns: dict[str, Any]) -> type[Any] | None:
    if pt is int:
        return _make_int_filter(ns)
    if pt is float:
        return _make_float_filter(ns)
    if pt in (str, bytes, dict, list, datetime.datetime, datetime.date, datetime.time, decimal.Decimal, uuid.UUID):
        return _make_string_filter(ns)
    if pt is bool:
        return _make_boolean_filter(ns)
    return _make_string_filter(ns)


# ---------------------------------------------------------------------------
# Shared PageInfo type
# ---------------------------------------------------------------------------


def _make_page_info(ns: dict[str, Any]) -> type[Any]:
    name = "PageInfo"
    if name in ns:
        return cast("type[Any]", ns[name])
    fields = [
        ("hasNextPage", bool, dataclasses.field(default=False)),
        ("hasPreviousPage", bool, dataclasses.field(default=False)),
        ("startCursor", Optional[str], dataclasses.field(default=None)),
        ("endCursor", Optional[str], dataclasses.field(default=None)),
    ]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.type(dc)
    ns[name] = t
    return cast("type[Any]", t)


# ---------------------------------------------------------------------------
# Per-table dynamic type factories
# ---------------------------------------------------------------------------


def _make_output_type(table: TableInfo, ns: dict[str, Any]) -> type[Any]:
    fields: list[Any] = []
    for col in table.columns:
        gt = _gql_type(col.python_type)
        if col.nullable:
            fields.append((col.name, Optional[gt], dataclasses.field(default=None)))
        else:
            fields.append((col.name, gt, dataclasses.field(default=None)))
    name = _pascal(_safe_name(table.name))
    if not fields:
        fields = [("_placeholder", str, dataclasses.field(default=""))]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.type(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _make_create_input(table: TableInfo, ns: dict[str, Any]) -> type[Any]:
    fields: list[Any] = []
    for col in table.columns:
        if col.is_primary_key:
            gt = _gql_type(col.python_type)
            fields.append((col.name, Optional[gt], dataclasses.field(default=strawberry.UNSET)))
            continue
        gt = _gql_type(col.python_type)
        if col.nullable or col.default is not None:
            fields.append((col.name, Optional[gt], dataclasses.field(default=strawberry.UNSET)))
        else:
            fields.append((col.name, gt, dataclasses.field(default=strawberry.UNSET)))
    name = _pascal(_safe_name(table.name)) + "CreateInput"
    if not fields:
        fields = [("_placeholder", Optional[str], dataclasses.field(default=None))]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _make_update_input(table: TableInfo, ns: dict[str, Any]) -> type[Any]:
    fields: list[Any] = []
    for col in table.columns:
        if col.is_primary_key:
            continue
        gt = _gql_type(col.python_type)
        fields.append((col.name, Optional[gt], dataclasses.field(default=strawberry.UNSET)))
    name = _pascal(_safe_name(table.name)) + "UpdateInput"
    if not fields:
        fields = [("_placeholder", Optional[str], dataclasses.field(default=None))]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _make_table_filter(table: TableInfo, ns: dict[str, Any]) -> type[Any]:
    """Create a per-table filter input type (e.g. UsersFilter)."""
    pascal = _pascal(_safe_name(table.name))
    name = pascal + "Filter"
    if name in ns:
        return cast("type[Any]", ns[name])
    fields: list[Any] = []
    for col in table.columns:
        ft = _filter_type_for_python_type(col.python_type, ns)
        if ft is not None:
            fields.append((col.name, Optional[ft], dataclasses.field(default=None)))
    if not fields:
        fields = [("_placeholder", Optional[str], dataclasses.field(default=None))]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return cast("type[Any]", t)


def _make_edge_type(out_type: type[Any], ns: dict[str, Any]) -> type[Any]:
    out_name = out_type.__name__
    edge_name = out_name + "Edge"
    if edge_name in ns:
        return cast("type[Any]", ns[edge_name])
    fields = [
        ("node", out_type, dataclasses.field(default=None)),
        ("cursor", str, dataclasses.field(default="")),
    ]
    dc = dataclasses.make_dataclass(edge_name, fields)
    dc.__module__ = _MOD
    t = strawberry.type(dc)
    ns[edge_name] = t
    return cast("type[Any]", t)


def _make_connection_type(out_type: type[Any], edge_type: type[Any], ns: dict[str, Any]) -> type[Any]:
    out_name = out_type.__name__
    conn_name = out_name + "Connection"
    if conn_name in ns:
        return cast("type[Any]", ns[conn_name])
    page_info_type = _make_page_info(ns)
    fields: list[Any] = [
        ("edges", list[edge_type], dataclasses.field(default_factory=list)),  # type: ignore[valid-type]
        ("pageInfo", page_info_type, dataclasses.field(default=None)),
        ("totalCount", int, dataclasses.field(default=0)),
    ]
    dc = dataclasses.make_dataclass(conn_name, fields)
    dc.__module__ = _MOD
    t = strawberry.type(dc)
    ns[conn_name] = t
    return cast("type[Any]", t)


# ---------------------------------------------------------------------------
# SQLAlchemy table builder
# ---------------------------------------------------------------------------


def _sa_table(table: TableInfo, metadata: MetaData) -> Table:
    cols: list[Column[Any]] = []
    for col in table.columns:
        pt = col.python_type
        if pt is int:
            sa_t: Any = Integer()
        elif pt is float:
            sa_t = Float()
        elif pt is bool:
            sa_t = Boolean()
        else:
            sa_t = String()
        cols.append(Column(col.name, sa_t))
    return Table(table.name, metadata, *cols, extend_existing=True)


# ---------------------------------------------------------------------------
# Filter application
# ---------------------------------------------------------------------------


def _apply_filter_conditions(query: Any, sa_tbl: Table, filter_input: Any, col_fields: dict[str, list[str]] | None = None) -> Any:
    """Apply GraphQL filter input to a SQLAlchemy query."""
    if filter_input is None:
        return query
    from sqlalchemy import and_ as sa_and
    parts: list[Any] = []

    # P3-27: Use precomputed col_fields mapping when available to avoid
    # calling dataclasses.fields() on every request.
    if col_fields is not None:
        for col_name, ops in col_fields.items():
            op_input = getattr(filter_input, col_name, None)
            if op_input is None or col_name not in sa_tbl.c:
                continue
            col = sa_tbl.c[col_name]
            for op in ops:
                value = getattr(op_input, op, None)
                if value is None:
                    continue
                if op == "eq":
                    parts.append(col == value)
                elif op == "gt":
                    parts.append(col > value)
                elif op == "lt":
                    parts.append(col < value)
                elif op == "gte":
                    parts.append(col >= value)
                elif op == "lte":
                    parts.append(col <= value)
                elif op == "contains":
                    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    parts.append(col.like(f"%{escaped}%"))
                elif op == "startsWith":
                    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    parts.append(col.like(f"{escaped}%"))
    else:
        for f in dataclasses.fields(filter_input):
            col_name = f.name
            if col_name.startswith("_"):
                continue
            op_input = getattr(filter_input, col_name, None)
            if op_input is None or col_name not in sa_tbl.c:
                continue
            col = sa_tbl.c[col_name]
            for sf in dataclasses.fields(op_input):
                op = sf.name
                value = getattr(op_input, op, None)
                if value is None:
                    continue
                if op == "eq":
                    parts.append(col == value)
                elif op == "gt":
                    parts.append(col > value)
                elif op == "lt":
                    parts.append(col < value)
                elif op == "gte":
                    parts.append(col >= value)
                elif op == "lte":
                    parts.append(col <= value)
                elif op == "contains":
                    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    parts.append(col.like(f"%{escaped}%"))
                elif op == "startsWith":
                    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    parts.append(col.like(f"{escaped}%"))
    if parts:
        query = query.where(sa_and(*parts))
    return query


def _apply_search(query: Any, sa_tbl: Table, search: str | None, string_columns: set[str]) -> Any:
    if not search or not string_columns:
        return query
    from sqlalchemy import or_ as sa_or
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    or_parts: list[Any] = []
    for col_name in sorted(string_columns):
        if col_name in sa_tbl.c:
            or_parts.append(sa_tbl.c[col_name].like(pattern))
    if or_parts:
        query = query.where(sa_or(*or_parts))
    return query


# ---------------------------------------------------------------------------
# Resolver builder via exec so parameter names are exact
# ---------------------------------------------------------------------------


def _build_resolver(
    src: str,
    extra_globals: dict[str, Any],
    *,
    module: str = _MOD,
) -> Any:
    """Compile a resolver function with an isolated globals namespace.

    The compiled function gets a fresh dict containing this module's
    globals merged with the per-resolver ``extra_globals``. We MUST NOT
    write back into ``sys.modules[_MOD].__dict__`` — doing so leaks
    dynamically-built classes across generator instances and silently
    overwrites types of the same name on successive calls. See
    specs/05-graphql-api-generator.md (Generator isolation).

    ``module`` controls the resulting function's ``__module__``; by
    default it is this module, but generators pass an isolated fake
    module so Strawberry's type-hint resolution finds the per-call types.
    """
    globs: dict[str, Any] = {
        **globals(),
        **extra_globals,
    }
    exec(src, globs)
    fn = globs[_extract_fn_name(src)]
    fn.__module__ = module
    return fn


def _extract_fn_name(src: str) -> str:
    for line in src.splitlines():
        line = line.strip()
        if line.startswith(("async def ", "def ")):
            return line.split("(")[0].split()[-1]
    raise ValueError("Could not extract function name")


# ---------------------------------------------------------------------------
# Resolver factories
# ---------------------------------------------------------------------------


def _list_resolver(
    sa_tbl: Table,
    engine: AsyncEngine,
    out_type: type[Any],
    edge_type: type[Any],
    conn_type: type[Any],
    page_info_type: type[Any],
    filter_type: type[Any],
    pk_cols: list[str],
    string_columns: set[str],
    has_single_pk: bool,
    json_columns: frozenset[str] = frozenset(),
    col_fields: dict[str, list[str]] | None = None,
) -> Any:
    out_name = out_type.__name__
    edge_name = edge_type.__name__
    conn_name = conn_type.__name__
    page_info_name = page_info_type.__name__
    filter_name = filter_type.__name__

    src = f"""
async def resolver(
    first: int | None = None,
    after: str | None = None,
    last: int | None = None,
    before: str | None = None,
    filter: {filter_name} | None = None,
    search: str | None = None,
) -> {conn_name}:
    # Determine pagination direction and limit
    if first is not None and last is not None:
        last = None  # prefer forward
    if first is None and last is None:
        first = 20

    forward = last is None
    limit = first if forward else last
    limit = max(1, min(100, limit or 20))
    query_limit = limit + 1  # fetch one extra to detect hasMore

    base_q = select(sa_tbl)
    base_q = _apply_filter_conditions(base_q, sa_tbl, filter, col_fields)
    base_q = _apply_search(base_q, sa_tbl, search, string_columns)

    # totalCount must reflect the *filtered* result set (Relay semantics —
    # see specs/09-graphql-relay-filtering.md). Build a count query off
    # the same WHERE clause, before any cursor narrowing or limit.
    count_q = select(func.count()).select_from(sa_tbl)
    where_filtered = base_q.whereclause
    if where_filtered is not None:
        count_q = count_q.where(where_filtered)

    # Cursor-based PK filtering
    if has_single_pk and pk_cols:
        pk_col_name = pk_cols[0]
        pk_col = sa_tbl.c[pk_col_name]
        if after:
            try:
                after_val = _decode_cursor(after).get(pk_col_name)
                if after_val is not None:
                    base_q = base_q.where(pk_col > after_val)
            except ValueError:
                pass
        if before:
            try:
                before_val = _decode_cursor(before).get(pk_col_name)
                if before_val is not None:
                    base_q = base_q.where(pk_col < before_val)
            except ValueError:
                pass

    # Order and limit
    if has_single_pk and pk_cols and not forward:
        pk_col_name = pk_cols[0]
        base_q = base_q.order_by(sa_tbl.c[pk_col_name].desc())
    elif has_single_pk and pk_cols:
        pk_col_name = pk_cols[0]
        base_q = base_q.order_by(sa_tbl.c[pk_col_name].asc())
    base_q = base_q.limit(query_limit)

    async with engine.connect() as conn:
        total = (await conn.execute(count_q)).scalar_one()
        rows = (await conn.execute(base_q)).mappings().all()

        # P1-14: For backward pagination, hasNextPage must check if items
        # exist AFTER the before cursor (not just bool(before)).
        has_next_backward = False
        if not forward and before and has_single_pk and pk_cols:
            pk_col_name = pk_cols[0]
            pk_col = sa_tbl.c[pk_col_name]
            try:
                before_val = _decode_cursor(before).get(pk_col_name)
                if before_val is not None:
                    next_q = select(func.count()).select_from(sa_tbl)
                    if where_filtered is not None:
                        next_q = next_q.where(where_filtered)
                    next_q = next_q.where(pk_col >= before_val)
                    has_next_backward = (await conn.execute(next_q)).scalar_one() > 0
            except (ValueError, Exception):
                pass

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    if not forward and has_single_pk and pk_cols:
        rows = list(reversed(rows))

    edges = []
    for row in rows:
        row_dict = dict(row)
        _serialize_row(row_dict, json_columns)
        if pk_cols:
            cursor_vals = {{c: row_dict[c] for c in pk_cols}}
        else:
            cursor_vals = {{"offset": 0}}
        edges.append({edge_name}(node={out_name}(**row_dict), cursor=_encode_cursor(cursor_vals)))

    page_info = {page_info_name}(
        hasNextPage=(has_more if forward else has_next_backward),
        hasPreviousPage=(bool(after) if forward else has_more),
        startCursor=edges[0].cursor if edges else None,
        endCursor=edges[-1].cursor if edges else None,
    )

    return {conn_name}(edges=edges, pageInfo=page_info, totalCount=total)
"""
    return _build_resolver(src, {
        out_name: out_type,
        edge_name: edge_type,
        conn_name: conn_type,
        page_info_name: page_info_type,
        filter_name: filter_type,
        "sa_tbl": sa_tbl,
        "engine": engine,
        "pk_cols": pk_cols,
        "string_columns": string_columns,
        "has_single_pk": has_single_pk,
        "json_columns": json_columns,
        "col_fields": col_fields,
    })


def _byid_single_resolver(sa_tbl: Table, pk: str, engine: AsyncEngine, out_type: type[Any], pk_python_type: type[Any] = int, json_columns: frozenset[str] = frozenset()) -> Any:
    out_name = out_type.__name__
    gql_type = _gql_type(pk_python_type).__name__
    src = f"""
async def resolver(id: {gql_type}) -> Optional[{out_name}]:
    async with engine.connect() as conn:
        row = (await conn.execute(select(sa_tbl).where(sa_tbl.c[pk] == id))).mappings().first()
    if row is None:
        return None
    return {out_name}(**_serialize_row(dict(row), json_columns))
"""
    return _build_resolver(src, {out_name: out_type, "sa_tbl": sa_tbl, "pk": pk, "engine": engine, "json_columns": json_columns})


def _byid_composite_resolver(sa_tbl: Table, pk_cols: list[str], engine: AsyncEngine, out_type: type[Any], json_columns: frozenset[str] = frozenset()) -> Any:
    out_name = out_type.__name__
    params = ", ".join(f"{c}: int" for c in pk_cols)
    cond_expr = "[" + ", ".join(f"sa_tbl.c['{c}'] == {c}" for c in pk_cols) + "]"
    src = f"""
async def resolver({params}) -> Optional[{out_name}]:
    cond = {cond_expr}
    async with engine.connect() as conn:
        row = (await conn.execute(select(sa_tbl).where(*cond))).mappings().first()
    if row is None:
        return None
    return {out_name}(**_serialize_row(dict(row), json_columns))
"""
    return _build_resolver(src, {out_name: out_type, "sa_tbl": sa_tbl, "engine": engine, "json_columns": json_columns})


def _create_resolver(
    sa_tbl: Table, pk_cols: list[str], engine: AsyncEngine,
    out_type: type[Any], input_type: type[Any],
    json_columns: frozenset[str] = frozenset(),
) -> Any:
    out_name = out_type.__name__
    in_name = input_type.__name__
    pk_fetch: str
    if len(pk_cols) == 1:
        pk_fetch = f"select(sa_tbl).where(sa_tbl.c['{pk_cols[0]}'] == row_id)"
    else:
        pk_fetch = "select(sa_tbl).where(*[sa_tbl.c[c] == cleaned.get(c) for c in pk_cols])"

    src = f"""
async def resolver(input: {in_name}) -> {out_name}:
    cleaned = {{}}
    for f in dataclasses.fields(input):
        name = f.name
        if name.startswith("_placeholder"):
            continue
        v = getattr(input, name)
        if v is strawberry.UNSET:
            continue
        cleaned[name] = v
    async with engine.begin() as conn:
        try:
            result = await conn.execute(insert(sa_tbl).values(**cleaned))
            row_id = result.lastrowid
        except IntegrityError as exc:
            # P0-4 / spec 05: surface as a typed GraphQL error so clients
            # can branch on extensions.code rather than parsing free-form
            # text. A bare ``raise Exception(...)`` was previously masked
            # as "Internal server error" with no error code.
            raise GraphQLError(
                "Unique constraint violated",
                extensions={{"code": "CONFLICT"}},
            ) from exc
    async with engine.connect() as conn:
        row = (await conn.execute({pk_fetch})).mappings().first()
    if row is None:
        return {out_name}(**cleaned)
    return {out_name}(**_serialize_row(dict(row), json_columns))
"""
    return _build_resolver(src, {
        out_name: out_type, in_name: input_type,
        "sa_tbl": sa_tbl, "pk_cols": pk_cols, "engine": engine,
        "json_columns": json_columns,
    })


def _update_resolver(
    sa_tbl: Table, pk: str, engine: AsyncEngine,
    out_type: type[Any], input_type: type[Any],
    pk_python_type: type[Any] = int,
    json_columns: frozenset[str] = frozenset(),
) -> Any:
    out_name = out_type.__name__
    in_name = input_type.__name__
    gql_type = _gql_type(pk_python_type).__name__
    src = f"""
async def resolver(id: {gql_type}, input: {in_name}) -> Optional[{out_name}]:
    updates = {{}}
    for f in dataclasses.fields(input):
        name = f.name
        if name.startswith("_placeholder"):
            continue
        v = getattr(input, name)
        if v is strawberry.UNSET:
            continue
        updates[name] = v
    if updates:
        async with engine.begin() as conn:
            result = await conn.execute(update(sa_tbl).where(sa_tbl.c[pk] == id).values(**updates))
        if result.rowcount == 0:
            return None
    async with engine.connect() as conn:
        row = (await conn.execute(select(sa_tbl).where(sa_tbl.c[pk] == id))).mappings().first()
    if row is None:
        return None
    return {out_name}(**_serialize_row(dict(row), json_columns))
"""
    return _build_resolver(src, {
        out_name: out_type, in_name: input_type,
        "sa_tbl": sa_tbl, "pk": pk, "engine": engine,
        "json_columns": json_columns,
    })


def _delete_resolver(sa_tbl: Table, pk: str, engine: AsyncEngine, pk_python_type: type[Any] = int) -> Any:
    gql_type = _gql_type(pk_python_type).__name__
    src = f"""
async def resolver(id: {gql_type}) -> bool:
    async with engine.begin() as conn:
        result = await conn.execute(delete(sa_tbl).where(sa_tbl.c[pk] == id))
    return result.rowcount > 0
"""
    return _build_resolver(src, {"sa_tbl": sa_tbl, "pk": pk, "engine": engine})


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class GraphqlApiGenerator:
    # Monotonic counter for unique per-instance fake-module names.
    _instance_counter: int = 0

    def __init__(self, *, engine: AsyncEngine) -> None:
        self._engine = engine
        self._metadata = MetaData()

    def _make_isolated_module(self) -> types.ModuleType:
        """Create a per-call fake module to host dynamically-built types.

        Strawberry resolves type-hint forward references via
        ``sys.modules[fn.__module__]``. We give every ``generate()`` call
        its own isolated module so type names cannot collide or bleed
        between independent schemas. Crucially, the real
        ``dbzap.generators.graphql`` module's ``__dict__`` stays untouched.
        See specs/05-graphql-api-generator.md (Generator isolation).
        """
        GraphqlApiGenerator._instance_counter += 1
        mod_name = f"{_MOD}._isolated_{GraphqlApiGenerator._instance_counter}"
        mod = types.ModuleType(mod_name)
        # Seed with this module's static globals so resolver code can
        # reference imports (select, func, dataclasses, strawberry, ...).
        mod.__dict__.update(globals())
        # Restore __name__ — globals() copied the real module's __name__
        # in, which would defeat the whole point of isolating per-call.
        mod.__dict__["__name__"] = mod_name
        sys.modules[mod_name] = mod
        return mod

    def generate(self, tables: list[TableInfo]) -> strawberry.Schema:
        # Per-call namespace + per-call fake module — never touch the
        # real graphql module's __dict__.
        ns: dict[str, Any] = {}
        iso_mod = self._make_isolated_module()
        iso_name = iso_mod.__name__

        def _claim(*objs: Any) -> None:
            """Reassign __module__ to the isolated module and register types
            into its __dict__ so Strawberry's type-hint resolution finds them."""
            for obj in objs:
                obj.__module__ = iso_name
                # Only types/classes have __name__; resolvers are functions
                # with __name__ == 'resolver' which we don't want to register.
                if isinstance(obj, type):
                    iso_mod.__dict__[obj.__name__] = obj

        query_fields: dict[str, Any] = {}
        query_annotations: dict[str, Any] = {}
        mutation_fields: dict[str, Any] = {}
        mutation_annotations: dict[str, Any] = {}
        extra_types: list[type[Any]] = []

        for table in tables:
            if not table.primary_key:
                logger.warning("graphql_table_no_pk", table=table.name)

            out_type = _make_output_type(table, ns)
            create_input = _make_create_input(table, ns)
            update_input = _make_update_input(table, ns)
            filter_input = _make_table_filter(table, ns)
            # P3-27: Precompute column→operator field names at generate time
            # so runtime doesn't call dataclasses.fields() per request.
            col_fields: dict[str, list[str]] = {}
            for f in dataclasses.fields(filter_input):
                if f.name.startswith("_"):
                    continue
                # f.type is Optional[SomeFilter] — unwrap to get the actual type
                ft = f.type
                args = get_args(ft)
                resolved = None
                for arg in args:
                    if arg is type(None):
                        continue
                    if dataclasses.is_dataclass(arg):
                        resolved = arg
                        break
                if resolved is not None:
                    col_fields[f.name] = [sf.name for sf in dataclasses.fields(resolved)]
            sa_tbl = _sa_table(table, self._metadata)
            pk_cols = table.primary_key
            pascal = _pascal(_safe_name(table.name))
            camel = pascal[0].lower() + pascal[1:]
            has_single_pk = len(pk_cols) == 1

            string_columns = {
                col.name for col in table.columns
                if col.python_type is str
            }

            json_columns = frozenset(
                col.name for col in table.columns
                if col.python_type in (dict, list)
            )

            _claim(out_type, create_input, update_input, filter_input)
            extra_types.extend([out_type, create_input, update_input, filter_input])
            # Also stash auxiliary filter input types (IntFilter, StringFilter, etc.)
            # that may have been auto-created inside _make_table_filter.
            for name, obj in list(ns.items()):
                if isinstance(obj, type):
                    iso_mod.__dict__.setdefault(name, obj)
                    if obj.__module__ == _MOD:
                        obj.__module__ = iso_name

            # Relay connection types
            edge_type = _make_edge_type(out_type, ns)
            conn_type = _make_connection_type(out_type, edge_type, ns)
            page_info_type = _make_page_info(ns)
            _claim(edge_type, conn_type, page_info_type)
            extra_types.extend([edge_type, conn_type, page_info_type])

            # List query (Relay Connection)
            lr = _list_resolver(
                sa_tbl, self._engine, out_type, edge_type, conn_type,
                page_info_type, filter_input, pk_cols, string_columns, has_single_pk,
                json_columns, col_fields,
            )
            lr.__module__ = iso_name
            query_fields[camel] = strawberry.field(resolver=lr)
            query_annotations[camel] = conn_type

            # By-ID query
            if pk_cols:
                pk_col_info = next(
                    (c for c in table.columns if c.name == pk_cols[0]), None
                )
                pk_type = pk_col_info.python_type if pk_col_info else int
                if len(pk_cols) == 1:
                    bir = _byid_single_resolver(sa_tbl, pk_cols[0], self._engine, out_type, pk_type, json_columns)
                else:
                    bir = _byid_composite_resolver(sa_tbl, pk_cols, self._engine, out_type, json_columns)
                bir.__module__ = iso_name
                query_fields[f"{camel}ById"] = strawberry.field(resolver=bir)
                query_annotations[f"{camel}ById"] = Optional[out_type]

            # Create mutation
            cr = _create_resolver(sa_tbl, pk_cols, self._engine, out_type, create_input, json_columns)
            cr.__module__ = iso_name
            mutation_fields[f"create{pascal}"] = strawberry.field(resolver=cr)
            mutation_annotations[f"create{pascal}"] = out_type

            if not pk_cols or len(pk_cols) != 1:
                continue

            pk = pk_cols[0]

            # Update mutation
            ur = _update_resolver(sa_tbl, pk, self._engine, out_type, update_input, pk_type, json_columns)
            ur.__module__ = iso_name
            mutation_fields[f"update{pascal}"] = strawberry.field(resolver=ur)
            mutation_annotations[f"update{pascal}"] = Optional[out_type]

            # Delete mutation
            dr = _delete_resolver(sa_tbl, pk, self._engine, pk_type)
            dr.__module__ = iso_name
            mutation_fields[f"delete{pascal}"] = strawberry.field(resolver=dr)
            mutation_annotations[f"delete{pascal}"] = bool

        # Placeholder query for empty schema
        if not query_fields:
            src = "async def resolver() -> str:\n    return 'ok'\n"
            fn = _build_resolver(src, {})
            query_fields["_health"] = strawberry.field(resolver=fn)
            query_annotations["_health"] = str

        QueryClass = strawberry.type(
            type("Query", (), {"__annotations__": query_annotations, **query_fields})
        )

        MutationClass: type[Any] | None = None
        if mutation_fields:
            MutationClass = strawberry.type(
                type("Mutation", (), {"__annotations__": mutation_annotations, **mutation_fields})
            )

        return strawberry.Schema(
            query=QueryClass,
            mutation=MutationClass,
            types=extra_types,
        )

    def mount(self, app: FastAPI, schema: strawberry.Schema) -> None:
        router = GraphQLRouter(schema, graphql_ide="graphiql")
        app.include_router(router, prefix="/graphql")
