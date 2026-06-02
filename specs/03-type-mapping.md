# Feature: Type Mapping

## Goal
Define a deterministic mapping from SQL column types to Python types, used by the introspector and API generators.

## Scope
- In scope: PostgreSQL built-in types, SQLite types, common aliases (SERIAL, BIGSERIAL), array types, JSON/JSONB, UUID, timestamp variants
- Out of scope: Custom PostgreSQL types (composite types, range types), PostGIS types, domain types, user-defined enums (mapped to `str` as fallback)

## API Contract

Internal interface:

```python
def map_sql_type_to_python(sql_type: str) -> type:
    """
    Convert a SQL type string to a Python type.

    Args:
        sql_type: Raw SQL type, e.g. "VARCHAR(255)", "INTEGER", "TIMESTAMP WITH TIME ZONE"

    Returns:
        A Python type: int, float, str, bool, datetime, date, time,
        bytes, Decimal, UUID, dict, list, or Any as fallback.
    """
```

## Mapping Table

| SQL Type (normalized)                  | Python Type   |
| -------------------------------------- | ------------- |
| `INTEGER`, `INT`, `SMALLINT`, `SERIAL` | `int`         |
| `BIGINT`, `BIGSERIAL`                  | `int`         |
| `FLOAT`, `REAL`, `DOUBLE PRECISION`    | `float`       |
| `NUMERIC`, `DECIMAL`                   | `Decimal`     |
| `BOOLEAN`                              | `bool`        |
| `VARCHAR`, `CHAR`, `TEXT`, `CLOB`      | `str`         |
| `DATE`                                 | `datetime.date` |
| `TIME`, `TIME WITHOUT TIME ZONE`       | `datetime.time` |
| `TIMESTAMP`, `TIMESTAMP WITHOUT TIME ZONE` | `datetime.datetime` |
| `TIMESTAMP WITH TIME ZONE`, `TIMESTAMPTZ` | `datetime.datetime` |
| `BYTEA`, `BLOB`                        | `bytes`       |
| `UUID`                                 | `uuid.UUID`   |
| `JSON`, `JSONB`                        | `dict`        |
| `ARRAY(...)` / `<type>[]`              | `list`        |
| Anything else                          | `Any`         |

Normalization: uppercase, strip length/precision suffixes (e.g. `VARCHAR(255)` -> `VARCHAR`).

## Data Model

No tables. Pure function, no state.

## Edge Cases
- `VARCHAR(255)`, `VARCHAR(100)`, `VARCHAR` all normalize to `str`.
- `NUMERIC(10, 2)` and `DECIMAL` both map to `Decimal`.
- PostgreSQL `TIMESTAMPTZ` shorthand must map to `datetime`.
- Unknown type returns `Any`, never raises.
- Case-insensitive: `varchar`, `VARCHAR`, `VarChar` all produce `str`.
- Array notation: `INTEGER[]` maps to `list`, `TEXT[]` maps to `list`.

## Acceptance Criteria
- [ ] All types in the mapping table are correctly converted.
- [ ] Type string normalization is case-insensitive and strips size modifiers.
- [ ] Unknown types return `Any` without raising.
- [ ] Function is pure (no side effects, no state).
- [ ] Mapping covers both PostgreSQL and SQLite type names.

## Module Location
`src/dbzap/core/type_mapping.py`

## Dependencies
- Standard library only (`datetime`, `decimal`, `uuid`, `typing`)
