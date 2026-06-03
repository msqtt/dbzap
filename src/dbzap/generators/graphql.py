"""GraphQL API generator: builds a Strawberry schema with CRUD for every table."""
from __future__ import annotations

import base64
import dataclasses
import datetime
import decimal
import json
import re
import sys
import uuid
from typing import Any, Optional

import strawberry
import structlog
from fastapi import FastAPI
from sqlalchemy import Table, Column, MetaData, Integer, String, Float, Boolean, func
from sqlalchemy import select, insert, update, delete
from sqlalchemy.exc import IntegrityError
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


def _encode_cursor(pk_values: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(pk_values).encode()).decode()


def _decode_cursor(token: str) -> dict[str, Any]:
    try:
        return json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except Exception as exc:
        raise ValueError(f"Invalid cursor: {token!r}") from exc


# ---------------------------------------------------------------------------
# Shared filter input types
# ---------------------------------------------------------------------------


def _make_int_filter(ns: dict[str, Any]) -> type[Any]:
    name = "IntFilter"
    if name in ns:
        return ns[name]
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
    return t


def _make_float_filter(ns: dict[str, Any]) -> type[Any]:
    name = "FloatFilter"
    if name in ns:
        return ns[name]
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
    return t


def _make_string_filter(ns: dict[str, Any]) -> type[Any]:
    name = "StringFilter"
    if name in ns:
        return ns[name]
    fields = [
        ("eq", Optional[str], dataclasses.field(default=None)),
        ("contains", Optional[str], dataclasses.field(default=None)),
        ("startsWith", Optional[str], dataclasses.field(default=None)),
    ]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return t


def _make_boolean_filter(ns: dict[str, Any]) -> type[Any]:
    name = "BooleanFilter"
    if name in ns:
        return ns[name]
    fields = [
        ("eq", Optional[bool], dataclasses.field(default=None)),
    ]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return t


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
        return ns[name]
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
    return t


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
    return t


def _make_create_input(table: TableInfo, ns: dict[str, Any]) -> type[Any]:
    fields: list[Any] = []
    for col in table.columns:
        if col.is_primary_key:
            gt = _gql_type(col.python_type)
            fields.append((col.name, Optional[gt], dataclasses.field(default=None)))
            continue
        gt = _gql_type(col.python_type)
        if col.nullable or col.default is not None:
            fields.append((col.name, Optional[gt], dataclasses.field(default=None)))
        else:
            fields.append((col.name, gt, dataclasses.field(default=strawberry.UNSET)))
    name = _pascal(_safe_name(table.name)) + "CreateInput"
    if not fields:
        fields = [("_placeholder", Optional[str], dataclasses.field(default=None))]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return t


def _make_update_input(table: TableInfo, ns: dict[str, Any]) -> type[Any]:
    fields: list[Any] = []
    for col in table.columns:
        if col.is_primary_key:
            continue
        gt = _gql_type(col.python_type)
        fields.append((col.name, Optional[gt], dataclasses.field(default=None)))
    name = _pascal(_safe_name(table.name)) + "UpdateInput"
    if not fields:
        fields = [("_placeholder", Optional[str], dataclasses.field(default=None))]
    dc = dataclasses.make_dataclass(name, fields)
    dc.__module__ = _MOD
    t = strawberry.input(dc)
    ns[name] = t
    return t


def _make_table_filter(table: TableInfo, ns: dict[str, Any]) -> type[Any]:
    """Create a per-table filter input type (e.g. UsersFilter)."""
    pascal = _pascal(_safe_name(table.name))
    name = pascal + "Filter"
    if name in ns:
        return ns[name]
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
    return t


def _make_edge_type(out_type: type[Any], ns: dict[str, Any]) -> type[Any]:
    out_name = out_type.__name__
    edge_name = out_name + "Edge"
    if edge_name in ns:
        return ns[edge_name]
    fields = [
        ("node", out_type, dataclasses.field(default=None)),
        ("cursor", str, dataclasses.field(default="")),
    ]
    dc = dataclasses.make_dataclass(edge_name, fields)
    dc.__module__ = _MOD
    t = strawberry.type(dc)
    ns[edge_name] = t
    return t


def _make_connection_type(out_type: type[Any], edge_type: type[Any], ns: dict[str, Any]) -> type[Any]:
    out_name = out_type.__name__
    conn_name = out_name + "Connection"
    if conn_name in ns:
        return ns[conn_name]
    page_info_type = _make_page_info(ns)
    fields = [
        ("edges", list[edge_type], dataclasses.field(default_factory=list)),  # type: ignore[valid-type]
        ("pageInfo", page_info_type, dataclasses.field(default=None)),
        ("totalCount", int, dataclasses.field(default=0)),
    ]
    dc = dataclasses.make_dataclass(conn_name, fields)
    dc.__module__ = _MOD
    t = strawberry.type(dc)
    ns[conn_name] = t
    return t


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


def _apply_filter_conditions(query: Any, sa_tbl: Table, filter_input: Any) -> Any:
    """Apply GraphQL filter input to a SQLAlchemy query."""
    if filter_input is None:
        return query
    raw = dataclasses.asdict(filter_input)
    from sqlalchemy import and_ as sa_and
    parts: list[Any] = []
    for col_name, op_dict in raw.items():
        if col_name.startswith("_") or op_dict is None:
            continue
        if col_name not in sa_tbl.c:
            continue
        col = sa_tbl.c[col_name]
        for op, value in op_dict.items():
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


def _build_resolver(src: str, extra_globals: dict[str, Any]) -> Any:
    """Compile a resolver function, injecting types into its globals namespace."""
    globs: dict[str, Any] = {
        **sys.modules[_MOD].__dict__,
        **extra_globals,
    }
    exec(src, globs)  # noqa: S102
    fn = globs[_extract_fn_name(src)]
    fn.__module__ = _MOD
    return fn


def _extract_fn_name(src: str) -> str:
    for line in src.splitlines():
        line = line.strip()
        if line.startswith("async def ") or line.startswith("def "):
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
    base_q = _apply_filter_conditions(base_q, sa_tbl, filter)
    base_q = _apply_search(base_q, sa_tbl, search, string_columns)

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
        total = (await conn.execute(select(func.count()).select_from(select(sa_tbl).subquery()))).scalar_one()
        rows = (await conn.execute(base_q)).mappings().all()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    if not forward and has_single_pk and pk_cols:
        rows = list(reversed(rows))

    edges = []
    for row in rows:
        row_dict = dict(row)
        if pk_cols:
            cursor_vals = {{c: row_dict[c] for c in pk_cols}}
        else:
            cursor_vals = {{"offset": 0}}
        edges.append({edge_name}(node={out_name}(**row_dict), cursor=_encode_cursor(cursor_vals)))

    page_info = {page_info_name}(
        hasNextPage=(has_more if forward else bool(before)),
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
    })


def _byid_single_resolver(sa_tbl: Table, pk: str, engine: AsyncEngine, out_type: type[Any]) -> Any:
    out_name = out_type.__name__
    src = f"""
async def resolver(id: int) -> Optional[{out_name}]:
    async with engine.connect() as conn:
        row = (await conn.execute(select(sa_tbl).where(sa_tbl.c[pk] == id))).mappings().first()
    if row is None:
        return None
    return {out_name}(**dict(row))
"""
    return _build_resolver(src, {out_name: out_type, "sa_tbl": sa_tbl, "pk": pk, "engine": engine})


def _byid_composite_resolver(sa_tbl: Table, pk_cols: list[str], engine: AsyncEngine, out_type: type[Any]) -> Any:
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
    return {out_name}(**dict(row))
"""
    return _build_resolver(src, {out_name: out_type, "sa_tbl": sa_tbl, "engine": engine})


def _create_resolver(
    sa_tbl: Table, pk_cols: list[str], engine: AsyncEngine,
    out_type: type[Any], input_type: type[Any],
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
    raw = dataclasses.asdict(input)
    cleaned = {{
        k: v for k, v in raw.items()
        if v is not None and v is not strawberry.UNSET and not k.startswith("_placeholder")
    }}
    async with engine.begin() as conn:
        try:
            result = await conn.execute(insert(sa_tbl).values(**cleaned))
            row_id = result.lastrowid
        except IntegrityError as exc:
            raise Exception("Unique constraint violated") from exc
    async with engine.connect() as conn:
        row = (await conn.execute({pk_fetch})).mappings().first()
    if row is None:
        return {out_name}(**cleaned)
    return {out_name}(**dict(row))
"""
    return _build_resolver(src, {
        out_name: out_type, in_name: input_type,
        "sa_tbl": sa_tbl, "pk_cols": pk_cols, "engine": engine,
    })


def _update_resolver(
    sa_tbl: Table, pk: str, engine: AsyncEngine,
    out_type: type[Any], input_type: type[Any],
) -> Any:
    out_name = out_type.__name__
    in_name = input_type.__name__
    src = f"""
async def resolver(id: int, input: {in_name}) -> Optional[{out_name}]:
    raw = dataclasses.asdict(input)
    updates = {{k: v for k, v in raw.items() if v is not None and not k.startswith("_placeholder")}}
    if updates:
        async with engine.begin() as conn:
            result = await conn.execute(update(sa_tbl).where(sa_tbl.c[pk] == id).values(**updates))
        if result.rowcount == 0:
            return None
    async with engine.connect() as conn:
        row = (await conn.execute(select(sa_tbl).where(sa_tbl.c[pk] == id))).mappings().first()
    if row is None:
        return None
    return {out_name}(**dict(row))
"""
    return _build_resolver(src, {
        out_name: out_type, in_name: input_type,
        "sa_tbl": sa_tbl, "pk": pk, "engine": engine,
    })


def _delete_resolver(sa_tbl: Table, pk: str, engine: AsyncEngine) -> Any:
    src = f"""
async def resolver(id: int) -> bool:
    async with engine.begin() as conn:
        result = await conn.execute(delete(sa_tbl).where(sa_tbl.c[pk] == id))
    return result.rowcount > 0
"""
    return _build_resolver(src, {"sa_tbl": sa_tbl, "pk": pk, "engine": engine})


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class GraphqlApiGenerator:
    def __init__(self, *, engine: AsyncEngine) -> None:
        self._engine = engine
        self._metadata = MetaData()

    def generate(self, tables: list[TableInfo]) -> strawberry.Schema:
        # Shared namespace so resolver code can reference generated types by name
        ns: dict[str, Any] = {}

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
            sa_tbl = _sa_table(table, self._metadata)
            pk_cols = table.primary_key
            pascal = _pascal(_safe_name(table.name))
            camel = pascal[0].lower() + pascal[1:]
            has_single_pk = len(pk_cols) == 1

            string_columns = {
                col.name for col in table.columns
                if col.python_type is str
            }

            extra_types.extend([out_type, create_input, update_input, filter_input])

            # Update module-level namespace so resolvers can reference these types
            sys.modules[_MOD].__dict__.update(ns)

            # Relay connection types
            edge_type = _make_edge_type(out_type, ns)
            conn_type = _make_connection_type(out_type, edge_type, ns)
            page_info_type = _make_page_info(ns)
            extra_types.extend([edge_type, conn_type, page_info_type])
            sys.modules[_MOD].__dict__.update(ns)

            # List query (Relay Connection)
            lr = _list_resolver(
                sa_tbl, self._engine, out_type, edge_type, conn_type,
                page_info_type, filter_input, pk_cols, string_columns, has_single_pk,
            )
            query_fields[camel] = strawberry.field(resolver=lr)
            query_annotations[camel] = conn_type

            # By-ID query
            if pk_cols:
                if len(pk_cols) == 1:
                    bir = _byid_single_resolver(sa_tbl, pk_cols[0], self._engine, out_type)
                else:
                    bir = _byid_composite_resolver(sa_tbl, pk_cols, self._engine, out_type)
                query_fields[f"{camel}ById"] = strawberry.field(resolver=bir)
                query_annotations[f"{camel}ById"] = Optional[out_type]

            # Create mutation
            cr = _create_resolver(sa_tbl, pk_cols, self._engine, out_type, create_input)
            mutation_fields[f"create{pascal}"] = strawberry.field(resolver=cr)
            mutation_annotations[f"create{pascal}"] = out_type

            if not pk_cols or len(pk_cols) != 1:
                continue

            pk = pk_cols[0]

            # Update mutation
            ur = _update_resolver(sa_tbl, pk, self._engine, out_type, update_input)
            mutation_fields[f"update{pascal}"] = strawberry.field(resolver=ur)
            mutation_annotations[f"update{pascal}"] = Optional[out_type]

            # Delete mutation
            dr = _delete_resolver(sa_tbl, pk, self._engine)
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
