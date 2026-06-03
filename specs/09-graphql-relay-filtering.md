# Feature: GraphQL Relay Connections & Advanced Filtering

## Goal
Upgrade the GraphQL API generator to use Relay Cursor Connections for pagination and provide rich per-column filter Input types with operator support.

## Scope
- In scope:
  - Replace offset pagination with Relay Connection model (`edges`, `node`, `cursor`, `pageInfo`)
  - Generate per-table `Filter` input types with per-column operator support
  - Generate per-column-type operator input types (`IntFilter`, `StringFilter`, `FloatFilter`, `BooleanFilter`)
  - Add global text search (`search: String`) on list queries
  - Cursor encoding/decoding using base64-encoded PK values
  - Support `first`/`after` (forward) and `last`/`before` (backward) pagination
  - Update resolver logic to apply filters and pagination in SQL
- Out of scope:
  - Nested relation queries (keep flat table queries)
  - Subscriptions
  - Sorting / ordering (can be added later)
  - Batch mutations

## API Contract

### Generated Schema Example (users table)

```graphql
# Core Relay types (shared across all tables)
type PageInfo {
  hasNextPage: Boolean!
  hasPreviousPage: Boolean!
  startCursor: String
  endCursor: String
}

# Operator filter inputs (shared per Python type)
input IntFilter {
  eq: Int
  gt: Int
  lt: Int
  gte: Int
  lte: Int
}

input FloatFilter {
  eq: Float
  gt: Float
  lt: Float
  gte: Float
  lte: Float
}

input StringFilter {
  eq: String
  contains: String
  startsWith: String
}

input BooleanFilter {
  eq: Boolean
}

# Per-table filter input
input UsersFilter {
  id: IntFilter
  name: StringFilter
  email: StringFilter
  score: FloatFilter
}

# Per-table connection types
type UsersEdge {
  node: Users!
  cursor: String!
}

type UsersConnection {
  edges: [UsersEdge!]!
  pageInfo: PageInfo!
  totalCount: Int!
}

# Queries
type Query {
  users(
    first: Int
    after: String
    last: Int
    before: String
    filter: UsersFilter
    search: String
  ): UsersConnection!
  usersById(id: Int!): Users
}
```

### Pagination Behavior

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `first`   | 20      | 1-100 | Forward: return first N rows after `after` cursor |
| `after`   | null    | —     | Base64 cursor; return rows with PK > decoded value |
| `last`    | null    | 1-100 | Backward: return last N rows before `before` cursor |
| `before`  | null    | —     | Base64 cursor; return rows with PK < decoded value |

- `first` + `after` = forward pagination
- `last` + `before` = backward pagination
- If neither `first` nor `last` provided, default to `first: 20`
- `first` and `last` are clamped to [1, 100]
- Cursor encodes the primary key value as base64 JSON: `eyJpZCI6IDQyfQ==`
- Only single integer PK tables support cursor pagination; others fall back to limit/offset with `first`/`after` treated as limit/offset

### Filtering Behavior

- All filter conditions are combined with AND
- Each column filter supports only the operators valid for its type
- String `contains` uses SQL `LIKE '%value%'`
- String `startsWith` uses SQL `LIKE 'value%'`
- `search` performs a global OR search across all string/text columns using `LIKE '%value%'`
- Empty or null filter fields are ignored

## Data Model

No new database tables. Uses existing introspected schema.

## Edge Cases

- Table with no primary key: still gets list query with Relay Connection, but cursor encoding falls back to row index offset
- Table with composite PK: cursor encodes all PK columns as a JSON object; filtering still works
- Empty filter object: no filtering applied, returns all rows
- `search` on table with no string columns: silently ignored (returns all rows)
- Invalid base64 cursor: return GraphQL error with message "Invalid cursor"
- `first` and `last` both provided: prefer `first` (forward)

## Acceptance Criteria

- [ ] `users(first: 2)` returns Relay Connection with `edges`, `pageInfo`, `totalCount`
- [ ] `users(after: "...")` returns rows after the decoded cursor
- [ ] `users(filter: { name: { contains: "Al" } })` returns matching rows
- [ ] `users(search: "alice")` returns rows where any string column contains "alice"
- [ ] Combined `filter` and `search` work together (AND logic)
- [ ] Cursor pagination works for tables with single integer PK
- [ ] `pageInfo.hasNextPage` / `hasPreviousPage` are computed correctly
- [ ] All existing CRUD mutations still work unchanged
- [ ] Empty database still produces valid schema
- [ ] All SQL queries use parameterized statements

## Module Location
- `src/dbzap/generators/graphql.py` — generator implementation
- `tests/test_generators/test_graphql.py` — tests

## Dependencies
- `strawberry-graphql`
- `fastapi`
- `sqlalchemy[asyncio]`
- `dbzap.generators.filter` — reuse `parse_filters` / `apply_filters` patterns where applicable
