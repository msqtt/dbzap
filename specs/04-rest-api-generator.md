# Feature: REST API Generator

## Goal
Given an introspected database schema, dynamically generate FastAPI CRUD routes (Create, Read, Update, Delete, List) for every table.

## Scope
- In scope: CRUD operations per table, request/response Pydantic model generation from schema, pagination for list endpoints, filtering by primary key, input validation based on column constraints, OpenAPI docs
- Out of scope: Nested resource routes, batch operations, file upload, custom business logic hooks, rate limiting

## API Contract

For each table `users` with columns `id (PK, int)`, `name (str, NOT NULL)`, `email (str, UNIQUE)`:

```
POST   /api/users          -> Create a new row, return 201
GET    /api/users           -> List rows (paginated, filterable), return 200
GET    /api/users/{id}      -> Get single row by PK, return 200 or 404
PUT    /api/users/{id}      -> Full update, return 200 or 404
PATCH  /api/users/{id}      -> Partial update, return 200 or 404
DELETE /api/users/{id}      -> Delete row, return 204 or 404
```

### Pagination

Two modes are supported. The mode is determined by which query parameters the client sends:

#### Offset-based (default)

Activated when `page` or `page_size` is present (or no pagination params at all).

| Parameter   | Type  | Default | Description                      |
| ----------- | ----- | ------- | -------------------------------- |
| `page`      | int   | 1       | Page number (1-indexed, >= 1)    |
| `page_size` | int   | 20      | Items per page (clamped 1–100)   |

#### Cursor-based

Activated when `starting_after` or `ending_before` is present. Requires the table to have a single-column integer PK.

| Parameter        | Type   | Default | Description                                        |
| ---------------- | ------ | ------- | -------------------------------------------------- |
| `limit`          | int    | 20      | Items per page (clamped 1–100)                     |
| `starting_after` | string | —       | Base64 cursor — fetch items after this PK value    |
| `ending_before`  | string | —       | Base64 cursor — fetch items before this PK value   |

Cursor values are Base64-encoded PK values. The response includes `next_cursor` for chaining.

### Filtering (LHS Brackets)

Filter conditions use query parameters with the `field[op]=value` syntax. Multiple filters are ANDed by default.

#### Operators

| Operator | Meaning               | SQL equivalent          |
| -------- | --------------------- | ----------------------- |
| `eq`     | Equal                 | `= value`               |
| `ne`     | Not equal             | `!= value`              |
| `gt`     | Greater than          | `> value`               |
| `gte`    | Greater or equal      | `>= value`              |
| `lt`     | Less than             | `< value`               |
| `lte`    | Less or equal         | `<= value`              |
| `like`   | Pattern match         | `LIKE %value%`          |
| `in`     | In set                | `IN (v1, v2, ...)`      |
| `is`     | Null check            | `IS NULL` / `IS NOT NULL` |

Examples:
```
GET /api/users?name[like]=Alice           # name LIKE '%Alice%'
GET /api/users?score[gte]=80&score[lte]=100  # 80 <= score <= 100
GET /api/users?status[in]=active,pending  # status IN ('active','pending')
```

Plain field names without brackets are treated as `eq`:
```
GET /api/users?name=Alice                 # name = 'Alice'
```

#### OR combinations

Use the special `_or` parameter (comma-separated field references) to OR a group of conditions. All other top-level filters remain ANDed:

```
GET /api/users?name[like]=Alice&email[like]=bob&_or=name,email
```

Translates to: `WHERE (name LIKE '%Alice%' OR email LIKE '%bob%')`

Nested AND/OR via JSON `_filter` parameter:
```
GET /api/users?_filter={"or":[{"and":[{"field":"name","op":"like","value":"A"},{"field":"score","op":"gt","value":90}]},{"field":"email","op":"like","value":"admin"}]}
```

### Response format

#### Offset pagination response

```json
{
  "data": [
    { "id": 1, "name": "Alice" },
    { "id": 2, "name": "Bob" }
  ],
  "pagination": {
    "mode": "offset",
    "total_records": 105,
    "current_page": 2,
    "per_page": 20,
    "total_pages": 6,
    "has_next": true,
    "has_prev": true
  }
}
```

#### Cursor pagination response

```json
{
  "data": [
    { "id": 21, "name": "Carol" },
    { "id": 22, "name": "Dave" }
  ],
  "pagination": {
    "mode": "cursor",
    "has_next": true,
    "has_prev": true,
    "next_cursor": "MjI="
  }
}
```

Request/Response models are auto-generated:
```python
# Auto-generated for POST /api/users
class UsersCreate(BaseModel):
    name: str          # NOT NULL -> required
    email: str         # UNIQUE, NOT NULL -> required

# Auto-generated for PATCH /api/users/{id}
class UsersUpdate(BaseModel):
    name: str | None = None   # all fields optional
    email: str | None = None

# Auto-generated response
class UsersResponse(BaseModel):
    id: int
    name: str
    email: str
```

Generator interface:

```python
class RestApiGenerator:
    def generate(self, app: FastAPI, tables: list[TableInfo]) -> None:
        """Register CRUD routes on the given FastAPI app for all tables."""

    def generate_for_table(self, app: FastAPI, table: TableInfo) -> None:
        """Register CRUD routes for a single table."""
```

## Data Model

No new tables. Routes operate on the introspected database tables directly using SQLAlchemy Core (not ORM).

## Edge Cases
- Table with no primary key: skip CRUD-by-PK routes (GET/{id}, PUT, PATCH, DELETE), only generate POST and list GET. Log a warning.
- Table with composite primary key: PK path parameter becomes `{pk}` with columns joined by `/` (e.g. `/api/order_items/1/2` for `order_id=1, item_id=2`).
- Column with SQL default (e.g. `SERIAL`, `DEFAULT now()`): exclude from Create model (server-generated).
- Column with NOT NULL and no default: required in Create model.
- Column with UNIQUE constraint: validate uniqueness on create/update, return 409 on conflict.
- Very large table list: route registration happens at startup, not per-request.
- Invalid `page`/`page_size` values: clamp `page_size` to [1, 100], `page` to >= 1.
- Invalid `limit` value for cursor mode: clamp to [1, 100].
- Invalid cursor (malformed Base64 or non-existent PK): return 400 with descriptive error.
- Cursor pagination on table with composite PK or no PK: fall back to offset mode, ignore cursor params.
- Filter references non-existent column: silently ignore the filter (do not crash).
- Filter references non-existent operator: return 400 with descriptive error.
- `_or` references fields not present in query params: silently ignore those references.
- `_filter` JSON is malformed: return 400 with descriptive error.
- `like` operator: automatically wrap value with `%` on both sides for substring matching.
- `in` operator: split value by comma, trim whitespace.
- `is` operator: only accepts `null` or `not_null` as values.

## Acceptance Criteria
- [ ] All 6 CRUD routes are registered per table (when PK exists).
- [ ] Pydantic request models correctly reflect nullable/required fields.
- [ ] Pydantic response models include all columns.
- [ ] List endpoint supports offset pagination (`page`/`page_size`).
- [ ] List endpoint supports cursor pagination (`starting_after`/`ending_before`/`limit`).
- [ ] Cursor values are Base64-encoded; response includes `next_cursor`.
- [ ] List endpoint supports LHS Brackets filtering: `field[op]=value`.
- [ ] All 9 filter operators work: eq, ne, gt, gte, lt, lte, like, in, is.
- [ ] Multiple filters are ANDed by default.
- [ ] `_or` parameter groups specified fields into OR conditions.
- [ ] `_filter` JSON parameter supports nested AND/OR expressions.
- [ ] Response format uses `data` array and `pagination` metadata object.
- [ ] Offset mode response includes `mode`, `total_records`, `current_page`, `per_page`, `total_pages`, `has_next`, `has_prev`.
- [ ] Cursor mode response includes `mode`, `has_next`, `has_prev`, `next_cursor`.
- [ ] GET by PK returns 404 when row not found.
- [ ] DELETE returns 204 on success, 404 when row not found.
- [ ] PATCH updates only provided fields.
- [ ] OpenAPI docs (`/docs`) show all generated routes with correct schemas.
- [ ] Tables without PK get only POST and list routes.
- [ ] All SQL queries use parameterized statements.

## Module Location
`src/dbzap/generators/rest.py`

## Dependencies
- `fastapi`
- `sqlalchemy[asyncio]` (async session, Core queries)
- `pydantic` (model generation)
- `src/dbzap/core/introspector.py` (`TableInfo`)
- `src/dbzap/core/type_mapping.py` (Python types for model fields)
