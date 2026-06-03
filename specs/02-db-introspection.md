# Feature: Database Introspection

## Goal
Connect to a database, read its DDL, and produce a structured in-memory representation of all tables, columns, types, constraints, and relationships.

## Scope
- In scope: PostgreSQL (via asyncpg), MySQL/MariaDB (via aiomysql), SQLite (via aiosqlite for testing), table listing, column metadata (name, type, nullable, default, primary key), unique constraints, foreign keys, indexes
- Out of scope: Oracle, MSSQL, views, stored procedures, triggers, partitions, schema-level permissions

## API Contract

Internal interface:

```python
@dataclass
class ColumnInfo:
    name: str
    sql_type: str              # raw SQL type string, e.g. "VARCHAR(255)"
    python_type: type          # mapped Python type
    nullable: bool
    is_primary_key: bool
    default: str | None        # SQL default expression
    is_unique: bool

@dataclass
class ForeignKeyInfo:
    source_column: str
    target_table: str
    target_column: str

@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    primary_key: list[str]     # column names forming the PK
    foreign_keys: list[ForeignKeyInfo]
    unique_constraints: list[list[str]]  # each inner list = column group

class SchemaIntrospector:
    async def introspect(self) -> list[TableInfo]:
        """Read all user tables from the connected database."""

    async def introspect_table(self, table_name: str) -> TableInfo:
        """Read metadata for a single table."""

    def get_cached_schema(self) -> list[TableInfo]:
        """Return the cached schema snapshot."""

    async def reload(self) -> list[TableInfo]:
        """Force re-introspection and update cache."""

    @property
    def last_reload_at(self) -> datetime | None:
        """Wall-clock time of the last successful introspect()/reload().

        ``None`` until the first successful run. Consumed by the health
        endpoint and the ``introspection_last_reload_timestamp`` metric —
        a real timestamp, NOT the current wall-clock at the call site.
        """
```

## Data Model

No persistent tables created by this module. Read-only introspection of existing database objects.

The introspected schema is cached in memory as `list[TableInfo]` after first call.

## Edge Cases
- Database with zero tables: return empty list, not an error.
- Table with composite primary key: `primary_key` list has multiple entries.
- Column with user-defined type or enum: store raw SQL type string, map `python_type` to `str` as fallback.
- Foreign key referencing a table outside the introspected set: still record the FK, do not validate the target exists.
- Schema change after startup: only reflected after explicit `reload()` call.
- Connection failure: raise `ConnectionError` with the database URL (password masked).
- Table name with special characters or reserved words: must be handled correctly via SQLAlchemy's reflection.

## Acceptance Criteria
- [ ] `introspect()` returns all user tables with complete column metadata.
- [ ] Primary keys (single and composite) are correctly identified.
- [ ] Foreign keys include source column, target table, and target column.
- [ ] Unique constraints are captured (both column-level and table-level).
- [ ] Nullable/non-nullable columns are correctly distinguished.
- [ ] Cached schema is returned on subsequent calls without re-querying the database.
- [ ] `reload()` forces a fresh introspection.
- [ ] `last_reload_at` is `None` before first introspect, and equals the wall-clock time of the most recent successful `introspect()` / `reload()` afterward.
- [ ] Connection errors produce clear, actionable error messages.
- [ ] `introspect_table()` errors caused by an unknown table name surface as a distinct lookup error, NOT as a generic `ConnectionError`.
- [ ] All database queries are async.

## Module Location
`src/dbzap/core/introspector.py`

## Dependencies
- `sqlalchemy[asyncio]` (async engine, `inspect()`)
- `asyncpg` (PostgreSQL driver)
- `aiosqlite` (SQLite driver, testing only)
- `src/dbzap/core/config.py` (database URL)
- `src/dbzap/core/type_mapping.py` (SQL → Python type resolution)
