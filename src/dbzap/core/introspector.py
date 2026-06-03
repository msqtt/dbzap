from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection, Inspector, make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from dbzap.core.config import Settings, get_settings
from dbzap.core.engine import build_engine
from dbzap.core.type_mapping import map_sql_type_to_python

logger: Any = structlog.get_logger(__name__)


@dataclass
class ColumnInfo:
    name: str
    sql_type: str
    python_type: type[Any]
    nullable: bool
    is_primary_key: bool
    default: str | None
    is_unique: bool


@dataclass
class ForeignKeyInfo:
    source_column: str
    target_table: str
    target_column: str


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    unique_constraints: list[list[str]] = field(default_factory=list)


class SchemaIntrospector:
    def __init__(
        self,
        *,
        engine: AsyncEngine | None = None,
        settings: Settings | None = None,
    ) -> None:
        if engine is not None:
            self._engine = engine
        else:
            # Reuse the same dialect-aware factory as the app — otherwise a
            # SQLite URL would crash here on ``pool_size`` kwargs.
            self._engine = build_engine(settings or get_settings())
        self._cache: list[TableInfo] | None = None

    async def introspect(self) -> list[TableInfo]:
        if self._cache is not None:
            return self._cache
        try:
            async with self._engine.connect() as conn:
                result = await conn.run_sync(self._reflect_all)
        except ConnectionError:
            raise
        except Exception as exc:
            masked = make_url(str(self._engine.url)).render_as_string(hide_password=True)
            raise ConnectionError(f"Failed to connect to {masked}") from exc
        self._cache = result
        return self._cache

    async def introspect_table(self, table_name: str) -> TableInfo:
        try:
            async with self._engine.connect() as conn:
                def _reflect_single(sync_conn: Connection) -> TableInfo:
                    insp: Inspector = sa_inspect(sync_conn)
                    return self._reflect_one(insp, table_name)

                return await conn.run_sync(_reflect_single)
        except ConnectionError:
            raise
        except Exception as exc:
            masked = make_url(str(self._engine.url)).render_as_string(hide_password=True)
            raise ConnectionError(f"Failed to connect to {masked}") from exc

    def get_cached_schema(self) -> list[TableInfo]:
        if self._cache is None:
            raise RuntimeError("Schema not yet introspected. Call introspect() first.")
        return self._cache

    async def reload(self) -> list[TableInfo]:
        self._cache = None
        return await self.introspect()

    def _reflect_all(self, sync_conn: Connection) -> list[TableInfo]:
        insp: Inspector = sa_inspect(sync_conn)
        table_names = insp.get_table_names()
        return [self._reflect_one(insp, name) for name in table_names]

    def _reflect_one(self, insp: Inspector, table_name: str) -> TableInfo:
        raw_cols = insp.get_columns(table_name)
        pk_info = insp.get_pk_constraint(table_name)
        fk_list = insp.get_foreign_keys(table_name)
        uq_list = insp.get_unique_constraints(table_name)

        pk_set = set(pk_info["constrained_columns"])
        single_uq_cols = {
            uq["column_names"][0]
            for uq in uq_list
            if len(uq["column_names"]) == 1
        }

        columns = [
            ColumnInfo(
                name=col["name"],
                sql_type=str(col["type"]),
                python_type=map_sql_type_to_python(str(col["type"])),
                nullable=bool(col["nullable"]),
                is_primary_key=col["name"] in pk_set,
                default=col.get("default"),
                is_unique=col["name"] in single_uq_cols,
            )
            for col in raw_cols
        ]

        foreign_keys = [
            ForeignKeyInfo(
                source_column=src,
                target_table=fk["referred_table"],
                target_column=tgt,
            )
            for fk in fk_list
            for src, tgt in zip(fk["constrained_columns"], fk["referred_columns"])
        ]

        return TableInfo(
            name=table_name,
            columns=columns,
            primary_key=list(pk_info["constrained_columns"]),
            foreign_keys=foreign_keys,
            unique_constraints=[list(uq["column_names"]) for uq in uq_list],
        )
