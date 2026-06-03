# Feature: GraphQL API Generator

> **⚠️ Status: Partially superseded.** Pagination and filter input shapes
> documented below have been **replaced** by the Relay Connection model
> defined in `09-graphql-relay-filtering.md`. The current implementation:
>
> - Returns `<Tbl>Connection { edges, pageInfo, totalCount }` — NOT
>   `<Tbl>Pagination { items, page, pageSize, total, pages }`.
> - Accepts `(first, after, last, before, filter, search)` — NOT
>   `(page, pageSize)`.
>
> Sections retained from this spec (still authoritative):
> - Generator interface (`GraphqlApiGenerator.generate` / `mount`)
> - CRUD mutation shapes (`createUsers`, `updateUsers`, `deleteUsers`)
> - Edge cases for tables without PK / composite PK
> - SQL → GraphQL type mapping table at the bottom
>
> When in doubt, prefer 09. Anything in this file that contradicts 09 is
> historical and pending removal.

## Goal
Given an introspected database schema, dynamically generate a Strawberry GraphQL schema with Query and Mutation types providing CRUD operations for every table.

## Scope
- In scope: Query (single get, list with pagination), Mutation (create, update, delete), auto-generated GraphQL types per table, input types for create/update, filtering by primary key
- Out of scope: Subscriptions, relay connections, custom scalar registration, nested relation queries, batch mutations, file upload

## API Contract

Single endpoint:

```
POST /graphql   -> GraphQL endpoint (with GraphiQL playground in debug mode)
```

For each table `users` with columns `id (PK, int)`, `name (str)`, `email (str)`:

```graphql
# Auto-generated types
type Users {
  id: Int!
  name: String!
  email: String!
}

input UsersCreateInput {
  name: String!
  email: String!
}

input UsersUpdateInput {
  name: String
  email: String
}

# Auto-generated queries
type Query {
  users(page: Int = 1, pageSize: Int = 20): UsersPagination!
  usersById(id: Int!): Users
}

# Auto-generated pagination type
type UsersPagination {
  items: [Users!]!
  page: Int!
  pageSize: Int!
  total: Int!
  pages: Int!
}

# Auto-generated mutations
type Mutation {
  createUsers(input: UsersCreateInput!): Users!
  updateUsers(id: Int!, input: UsersUpdateInput!): Users
  deleteUsers(id: Int!): Boolean!
}
```

Generator interface:

```python
class GraphqlApiGenerator:
    def generate(self, tables: list[TableInfo]) -> strawberry.Schema:
        """Build a Strawberry Schema with Query and Mutation types for all tables."""

    def mount(self, app: FastAPI, schema: strawberry.Schema) -> None:
        """Mount the GraphQL endpoint and GraphiQL on a FastAPI app."""
```

## Data Model

No new tables. Operates on introspected tables using SQLAlchemy Core (async).

## Edge Cases
- Table with no primary key: skip `byId` query, `update`, and `delete` mutations. Log a warning.
- Table with composite primary key: `byId` query takes multiple arguments (one per PK column). The argument type for each PK column MUST follow that column's mapped Python type (e.g. an `int` PK becomes `Int!`, a `varchar` PK becomes `String!`). The implementation MUST NOT hard-code `Int!` for every PK column.
- Column with SQL default: exclude from create input type.
- Column with NOT NULL and no default: required (`!`) in create input.
- Empty database (zero tables): generate a schema with a placeholder `Query` type (Strawberry requires at least one field).
- GraphQL type name collision (e.g. table named `query`): prefix with `Tbl_` to avoid conflicts with reserved GraphQL type names.
- `JSON`/`JSONB` columns: map to `str` (JSON-serialized) in GraphQL, since Strawberry doesn't have a built-in JSON scalar by default. Register `scalars.JSON` if available.
- **Generator isolation**: each call to `generate(...)` MUST produce a fully independent set of GraphQL types. The generator MUST NOT register the dynamically-created classes into `sys.modules[__name__].__dict__` or any other process-wide namespace, because doing so causes successive calls (different table sets, multiple test fixtures, multi-tenant setups) to bleed into each other and silently overwrite types with the same name. Resolver bodies that need type lookup must close over a per-call namespace dict instead.
- **Cursor encoding**: cursors carry the row's primary-key values base64-encoded. The encoder MUST handle non-JSON-native PK types (`datetime`, `date`, `UUID`, `Decimal`, `bytes`) by stringifying them; otherwise tables keyed on those types crash on every list query.

## Acceptance Criteria
- [ ] GraphQL type generated for each table with all columns as fields.
- [ ] `Query.users` returns paginated list; `Query.usersById` returns single row or `None`.
- [ ] `Mutation.createUsers` inserts a row and returns it.
- [ ] `Mutation.updateUsers` updates only provided fields, returns updated row or `None`.
- [ ] `Mutation.deleteUsers` deletes row, returns `True`/`False`.
- [ ] Input types correctly distinguish required vs optional fields.
- [ ] Tables without PK get only list query and create mutation.
- [ ] GraphiQL playground is available at `/graphql` in debug mode.
- [ ] All SQL queries use parameterized statements.
- [ ] Empty database produces a valid (non-crashing) schema.

## Module Location
`src/dbzap/generators/graphql.py`

## Dependencies
- `strawberry-graphql`
- `fastapi`
- `sqlalchemy[asyncio]`
- `src/dbzap/core/introspector.py` (`TableInfo`)
- `src/dbzap/core/type_mapping.py` (Python types for GraphQL field types)

## Type Mapping (SQL -> GraphQL)

| Python Type   | GraphQL Type |
| ------------- | ------------ |
| `int`         | `Int`        |
| `float`       | `Float`      |
| `str`         | `String`     |
| `bool`        | `Boolean`    |
| `datetime`    | `DateTime` (ISO scalar) |
| `date`        | `Date` (ISO scalar)     |
| `Decimal`     | `Decimal` (custom scalar) |
| `UUID`        | `UUID` (custom scalar) |
| Everything else | `String` (serialized) |
