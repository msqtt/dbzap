# Spec 00 — Architecture Overview & Index

## Purpose

This document is the single entry point for all specs. Before modifying any feature, consult this file to identify the blast radius and avoid missing related spec updates.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Clients                                 │
│              (curl / browser / GraphiQL / Explorer)              │
└──────────┬──────────────────────────────────────────┬───────────┘
           │ REST (JSON)                              │ GraphQL
           ▼                                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  Server Layer  [07-server-bootstrap]                              │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Pure ASGI Middleware Stack (outermost → innermost)          │  │
│  │  1. PerformanceMiddleware [10] — timing, metrics            │  │
│  │  2. GZipMiddleware [07] — compress responses ≥1KB           │  │
│  │  3. CORSMiddleware [07] — origin enforcement                │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Routers                                                     │  │
│  │  /api/{table}/*    REST CRUD          [04]                  │  │
│  │  /graphql          Strawberry GraphQL [05, 09a]             │  │
│  │  /auth/*           Login / Register   [06]                  │  │
│  │  /explorer/*       Static SPA + config[08]                  │  │
│  │  /healthz, /ready  Probes             [09b]                 │  │
│  │  /metrics          Prometheus export  [10]                  │  │
│  │  /docs, /openapi   Swagger (auth-gated) [07]                │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
           │                          │
           ▼                          ▼
┌─────────────────────┐    ┌─────────────────────────┐
│  Core Layer          │    │  Auth Layer              │
│  ┌───────────────┐   │    │  ┌──────────────────┐   │
│  │ Settings [01] │   │    │  │ UserStore [06]   │   │
│  │ Engine   [10] │   │    │  │ Passwords [06]   │   │
│  │ Introspector  │   │    │  │ Tokens [06]      │   │
│  │        [02]   │   │    │  │ RateLimit [06]   │   │
│  │ TypeMap [03]  │   │    │  │ Dependencies [06]│   │
│  └───────────────┘   │    │  └──────────────────┘   │
└──────────┬────────────┘    └────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│  Generator Layer                     │
│  ┌─────────────┐ ┌───────────────┐  │
│  │ REST Gen    │ │ GraphQL Gen   │  │
│  │ [04]        │ │ [05, 09a]     │  │
│  └──────┬──────┘ └───────┬───────┘  │
│         └────────┬────────┘          │
│           ┌──────┴──────┐            │
│           │ FilterEngine│            │
│           │ [04, 09a]   │            │
│           └─────────────┘            │
└──────────────────┬──────────────────┘
                   │
                   ▼
          ┌─────────────────┐
          │    Database      │
          │ PG / MySQL / SQLite │
          └─────────────────┘
```

## Request Lifecycle

A typical authenticated REST request flows as:

```
Client
  → PerformanceMiddleware (start timer, increment in_progress gauge)
    → GZipMiddleware
      → CORSMiddleware (origin check)
        → FastAPI Router
          → Auth Dependency (JWT/Basic → UserRecord)
            → Route Handler (rest.py generated function)
              → engine.connect() → SQL execute → row result
            ← dict response
          ← (Pydantic serialization or orjson Response)
        ← HTTP response
      ← (compress if ≥1KB)
    ← (record latency, status, decrement in_progress)
  ← Wire
```

For GraphQL: same outer flow, but the router delegates to Strawberry which invokes per-field resolvers (each acquiring its own connection).

## Spec Index

| # | File | Responsibility | Source Files |
|---|------|----------------|--------------|
| 00 | `00-overview.md` | This file: architecture, index, dependency graph | — |
| 01 | `01-config.md` | Env vars, Settings model, validation rules | `core/config.py` |
| 02 | `02-db-introspection.md` | Schema introspection, caching, reload, `last_reload_at` | `core/introspector.py` |
| 03 | `03-type-mapping.md` | SQL → Python type mapping table | `core/type_mapping.py` |
| 04 | `04-rest-api-generator.md` | REST CRUD route generation, LHS filtering, offset + cursor pagination, PUT vs PATCH semantics | `generators/rest.py`, `generators/filter.py` |
| 05 | `05-graphql-api-generator.md` | GraphQL schema generation (**partially superseded by 09a**) | `generators/graphql.py` |
| 06 | `06-auth.md` | JWT/Basic auth, register, rate limiting, timing-attack mitigations, bcrypt pre-hash | `auth/*` |
| 07 | `07-server-bootstrap.md` | FastAPI creation, middleware mounting, CORS policy | `server/app.py` |
| 08 | `08-api-explorer-frontend.md` | Explorer SPA, `/explorer/config` endpoint | `server/static/`, `server/app.py` |
| 09a | `09-graphql-relay-filtering.md` | Relay Connections, Filter Input types, cursor encoding, `totalCount` semantics | `generators/graphql.py`, `generators/filter.py` |
| 09b | `09-healthz.md` | `/healthz` liveness, `/ready` readiness, `/ready/detail` | `server/health.py` |
| 10 | `10-performance.md` | Connection pool, dialect-aware engine, ASGI middleware, Prometheus metrics, orjson | `core/engine.py`, `server/middleware.py`, `server/metrics.py` |

> **Note:** `09a` and `09b` share the `09` prefix due to historical ordering. They are unrelated features. A future renumber may fix this.

## Dependency Graph

Arrows mean "spec A defines contracts consumed by spec B".

```
01-config ─────────┬──▶ 02-db-introspection (DATABASE_URL, pool settings)
                   ├──▶ 06-auth (JWT_SECRET_KEY, AUTH_MODE, LOGIN_RATE_LIMIT_*)
                   ├──▶ 07-server-bootstrap (HOST, PORT, CORS_*)
                   ├──▶ 08-api-explorer (ENABLE_EXPLORER, EXPLORER_USERNAME)
                   └──▶ 10-performance (DB_POOL_*, DB_STATEMENT_TIMEOUT)

02-db-introspection ──┬──▶ 03-type-mapping (Column metadata → Python type)
                      └──▶ 09b-healthz (last_reload_at, connection probe)

03-type-mapping ──┬──▶ 04-rest-api-generator (Pydantic field types)
                  └──▶ 05/09a-graphql (Strawberry field types)

04-rest-api-generator ──▶ 10-performance (connection reuse, count opt, orjson)

05/09a-graphql ──▶ 10-performance (namespace isolation, field iteration opt)

06-auth ──┬──▶ 04-rest (all endpoints protected)
          ├──▶ 05/09a-graphql (all resolvers protected)
          └──▶ 08-api-explorer (login flow, seed_admin_user)

07-server-bootstrap ──┬──▶ 10-performance (middleware ordering)
                      └──▶ 09b-healthz (router mounting)

10-performance ──▶ 02-db-introspection (engine factory reuse)
```

## Change Impact Matrix

Before making changes, look up the row to find which specs to review:

| Changing… | Must check specs | Must check tests |
|-----------|-----------------|------------------|
| Env vars / Settings | 01, README | `test_server/test_app.py` |
| DB connection / Engine | 01, 02, 10 | `test_server/test_app.py`, `test_server/test_health.py` |
| Type mapping | 03, 04, 05/09a | `test_generators/test_rest.py`, `test_generators/test_graphql.py` |
| REST route behavior | 04, 10 | `test_generators/test_rest.py` |
| GraphQL schema/resolver | 05, 09a, 10 | `test_generators/test_graphql.py` |
| Auth / authorization | 06, 04, 05/09a, 08 | `test_auth/*` |
| Middleware | 07, 10 | `test_server/test_middleware.py` |
| Explorer UI | 08, 06 | `test_server/test_explorer.py` |
| Health checks | 09b, 02 | `test_server/test_health.py` |
| Metrics / monitoring | 10, 07 | `test_server/test_middleware.py` |
| Pagination / filtering | 04, 09a | `test_generators/test_rest.py`, `test_generators/test_graphql.py` |
| Error handling | 04 (REST), 05/09a (GQL), 06 (auth) | all test dirs |

## Source Module Map

```
src/dbzap/
├── core/
│   ├── config.py          ← 01-config
│   ├── engine.py          ← 01-config + 10-performance
│   ├── introspector.py    ← 02-db-introspection
│   └── type_mapping.py    ← 03-type-mapping
├── generators/
│   ├── rest.py            ← 04-rest-api-generator
│   ├── graphql.py         ← 05-graphql + 09a-relay-filtering
│   └── filter.py          ← 04-rest + 09a-graphql (shared)
├── auth/
│   ├── routes.py          ← 06-auth (login/register endpoints)
│   ├── dependencies.py    ← 06-auth (FastAPI Depends)
│   ├── passwords.py       ← 06-auth (bcrypt + SHA-256 pre-hash)
│   ├── tokens.py          ← 06-auth (JWT encode/decode)
│   ├── user_store.py      ← 06-auth (CRUD on _users table)
│   ├── rate_limit.py      ← 06-auth (sliding window per-IP)
│   └── models.py          ← 06-auth (UserRecord dataclass)
├── server/
│   ├── app.py             ← 07-server-bootstrap + 08-explorer
│   ├── middleware.py      ← 10-performance (pure ASGI)
│   ├── metrics.py         ← 10-performance (MetricsCollector)
│   ├── health.py          ← 09b-healthz
│   ├── __main__.py        ← CLI entry (serve / inspect / version / healthcheck)
│   └── static/            ← 08-api-explorer-frontend
```

## Internal vs User Tables

| Table | Owner | Notes |
|-------|-------|-------|
| `_users` | dbzap (auth) | Auto-created by `UserStore.initialize()`. Excluded from API generation. |
| Everything else | User's database | Discovered by introspector, exposed via REST + GraphQL. |

Tables prefixed with `_` or listed in `_INTERNAL_TABLES` (currently `{"_users"}`) are never exposed as API endpoints.

## Error Handling Strategy

| Layer | Error Type | HTTP Status | Format |
|-------|-----------|-------------|--------|
| Auth | Invalid credentials | 401 | `{"detail": "..."}` |
| Auth | Rate limited | 429 | `{"detail": "..."}` |
| REST | Validation (Pydantic) | 422 | `{"detail": [...errors]}` |
| REST | Not found | 404 | `{"detail": "..."}` |
| REST | Unique constraint | 409 | `{"detail": "..."}` |
| GraphQL | Validation | 200 | `{"errors": [{"message": "...", "extensions": {"code": "VALIDATION_ERROR"}}]}` |
| GraphQL | Unique constraint | 200 | `{"errors": [{"message": "...", "extensions": {"code": "CONFLICT"}}]}` |
| Health | DB unreachable | 503 | `{"status": "unhealthy", ...}` |
| Any | Unhandled | 500 | FastAPI default exception handler |

## Key Design Decisions

| # | Decision | Rationale | Spec |
|---|----------|-----------|------|
| 1 | Zero code generation — routes/schema built at runtime | No build step, instant schema changes on restart | 02, 04, 05 |
| 2 | Dialect-aware engine factory | PG/MySQL get pool_size + statement_timeout; SQLite uses StaticPool (incompatible with pool params) | 10 |
| 3 | Pure ASGI middleware (not BaseHTTPMiddleware) | Avoids 30-50% overhead from response materialization + cross-task bridging | 10 |
| 4 | GraphQL namespace isolation (per-call fake module) | Prevents `sys.modules` pollution between successive `generate()` calls | 05 |
| 5 | orjson on hot paths | 3-5x faster JSON serialization for explicit Response construction; FastAPI default paths use built-in Pydantic serializer | 10 |
| 6 | Constant-time auth (dummy bcrypt on unknown user) | Prevents username enumeration via timing side-channel | 06 |
| 7 | Relay-standard pagination (`totalCount` = filtered count) | Matches Relay spec; frontends can compute correct page counts | 09a |
| 8 | PUT = full replace, PATCH = partial update | Follows REST/HTTP semantics; PUT sets missing fields to NULL/default | 04 |
| 9 | Single-connection mutation (insert + read-back in one conn) | Halves pool round-trips per write request | 10 |

## Testing Strategy

```
tests/
├── test_auth/            ← 06-auth: passwords, routes, rate limit, user store
├── test_generators/      ← 04, 05, 09a: REST + GraphQL integration tests
├── test_server/          ← 07, 08, 09b, 10: app, explorer, health, middleware
└── conftest.py           ← Shared fixtures (currently minimal)
```

- **Test DB**: In-memory SQLite (`sqlite+aiosqlite://`) for speed.
- **Coverage**: `pytest-cov` with branch coverage enabled.
- **Lint**: `ruff check` (E/F/I/B/UP/SIM/C4/PIE/RUF rules).
- **Types**: `mypy --strict` on `src/` — target 0 errors.
- **CI**: `.github/workflows/ci.yml` runs all three on every push/PR.

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2026-06-04 | Initial creation | AI-assisted |
