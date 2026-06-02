"""GraphQL API generator: builds a Strawberry schema with CRUD for every table."""
from __future__ import annotations

import dataclasses
import datetime
import decimal
import re
import sys
import uuid
from typing import Any, Optional

import strawberry
import structlog
from fastapi import FastAPI
from sqlalchemy import Table, Column, MetaData, Integer, String, Float, Boolean
from sqlalchemy import select, insert, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from strawberry.annotation import StrawberryAnnotation
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
# Dynamic strawberry type factories
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


def _list_resolver(sa_tbl: Table, engine: AsyncEngine, out_type: type[Any]) -> Any:
    out_name = out_type.__name__
    src = f"""
async def resolver(offset: int = 0, limit: int = 20) -> list[{out_name}]:
    offset = max(0, offset)
    limit = max(1, min(100, limit))
    async with engine.connect() as conn:
        rows = (await conn.execute(select(sa_tbl).offset(offset).limit(limit))).mappings().all()
    return [{out_name}(**dict(r)) for r in rows]
"""
    return _build_resolver(src, {out_name: out_type, "sa_tbl": sa_tbl, "engine": engine})


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
            sa_tbl = _sa_table(table, self._metadata)
            pk_cols = table.primary_key
            pascal = _pascal(_safe_name(table.name))
            camel = pascal[0].lower() + pascal[1:]

            extra_types.extend([out_type, create_input, update_input])

            # Update module-level namespace so resolvers can reference these types
            sys.modules[_MOD].__dict__.update(ns)

            # List query
            lr = _list_resolver(sa_tbl, self._engine, out_type)
            query_fields[camel] = strawberry.field(resolver=lr)
            query_annotations[camel] = list[out_type]  # type: ignore[valid-type]

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
