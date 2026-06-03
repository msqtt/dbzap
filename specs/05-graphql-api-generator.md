# Feature: GraphQL API Generator

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
- Table with composite primary key: `byId` query takes multiple arguments (one per PK column).
- Column with SQL default: exclude from create input type.
- Column with NOT NULL and no default: required (`!`) in create input.
- Empty database (zero tables): generate a schema with a placeholder `Query` type (Strawberry requires at least one field).
- GraphQL type name collision (e.g. table named `query`): prefix with `Tbl_` to avoid conflicts with reserved GraphQL type names.
- `JSON`/`JSONB` columns: map to `str` (JSON-serialized) in GraphQL, since Strawberry doesn't have a built-in JSON scalar by default. Register `scalars.JSON` if available.

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
