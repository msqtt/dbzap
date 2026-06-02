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
GET    /api/users           -> List rows (paginated), return 200
GET    /api/users/{id}      -> Get single row by PK, return 200 or 404
PUT    /api/users/{id}      -> Full update, return 200 or 404
PATCH  /api/users/{id}      -> Partial update, return 200 or 404
DELETE /api/users/{id}      -> Delete row, return 204 or 404
```

Query parameters for list:
- `offset: int = 0`
- `limit: int = 20` (max 100)

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
- Invalid `limit`/`offset` values: clamp `limit` to [1, 100], `offset` to >= 0.

## Acceptance Criteria
- [ ] All 6 CRUD routes are registered per table (when PK exists).
- [ ] Pydantic request models correctly reflect nullable/required fields.
- [ ] Pydantic response models include all columns.
- [ ] List endpoint supports `offset`/`limit` pagination.
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
