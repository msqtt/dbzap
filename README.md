# ⚡ dbzap

[中文文档](README.zh-CN.md)

**Connect your database. Get every CRUD API. Done.**

dbzap reads your database schema and instantly generates REST + GraphQL APIs — with auth, docs, and a monitoring dashboard. Zero code generation, zero boilerplate.

```
┌─────────┐        ┌─────────┐        ┌──────────────┐
│ Database │───────▶│  dbzap  │───────▶│   REST API   │
│  (DDL)   │        │         │        │  GraphQL API │
└─────────┘        └─────────┘        │  /auth       │
                                      │  /explorer   │
                                      │  /metrics    │
                                      │  /healthz    │
                                      └──────────────┘
```

## Quick Start

### pip / poetry

```bash
# 1. Install
pip install dbzap        # or: poetry add dbzap

# 2. Configure
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost/mydb"
export JWT_SECRET_KEY="pick-a-strong-secret"

# 3. Run
dbzap serve
```

### Docker

```bash
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://user:pass@host/db" \
  -e JWT_SECRET_KEY="pick-a-strong-secret" \
  ghcr.io/msqtt/dbzap:latest
```

That's it. Every table in your database now has full CRUD endpoints.

- **REST**: `http://localhost:8000/docs` — Swagger UI
- **GraphQL**: `http://localhost:8000/graphql` — GraphiQL
- **Explorer**: `http://localhost:8000/explorer` — API testing + dashboard
- **Health**: `http://localhost:8000/healthz`

## What You Get

| Feature | Details |
|---------|---------|
| REST CRUD | `POST / GET / PUT / PATCH / DELETE` per table, auto-generated Pydantic models, offset + cursor pagination, LHS Brackets filtering |
| GraphQL | Query + Mutation per table, Relay Cursor Connections, Filter Input types with operators, global search |
| Auth | JWT login/register, all endpoints protected by default |
| API Explorer | Built-in UI to browse, test, and debug your APIs |
| Dashboard | Real-time metrics: request rate, latency, DB pool health |
| Health Check | `/healthz` liveness + readiness probes for K8s |
| Metrics | Prometheus-compatible `/metrics` endpoint |

## Configuration

All settings via environment variables (or `.env` file):

### Required

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Database connection URL. Examples:<br>`postgresql+asyncpg://user:pass@host/db`<br>`mysql+aiomysql://user:pass@host/db`<br>`sqlite+aiosqlite:///path/to/db.sqlite` |
| `JWT_SECRET_KEY` | HMAC secret for JWT token signing. Must be non-empty. |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `JWT_EXPIRE_MINUTES` | `60` | JWT token lifetime in minutes |
| `AUTH_MODE` | `jwt` | Authentication mode: `jwt`, `basic`, or `both` |
| `API_MODE` | `both` | Enabled APIs: `rest`, `graphql`, or `both` |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `ENABLE_EXPLORER` | `true` | Enable the web-based API Explorer UI |
| `EXPLORER_USERNAME` | — | Pre-fill explorer login username (optional) |
| `EXPLORER_PASSWORD` | — | Pre-fill explorer login password (optional) |
| `DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `20` | Max extra connections beyond pool size |
| `DB_POOL_TIMEOUT` | `30` | Seconds to wait for a connection from the pool |
| `DB_POOL_RECYCLE` | `1800` | Max connection lifetime in seconds |
| `DB_STATEMENT_TIMEOUT` | `5s` | Database statement timeout (e.g. `5s`, `30s`) |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins (JSON list or comma-separated) |
| `CORS_ALLOW_CREDENTIALS` | `false` | Allow credentials in CORS requests (incompatible with wildcard origin) |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | Max login attempts per IP per minute. `0` disables |

## Development

```bash
poetry install
cp .env.example .env     # edit with your DB credentials
poetry run pytest
poetry run mypy src/
```

## How It Works

1. **Introspect** — connects to your database, reads DDL (tables, columns, types, constraints, foreign keys)
2. **Map** — converts SQL types to Python types via a deterministic mapping table
3. **Generate** — builds FastAPI routes (REST) and Strawberry schema (GraphQL) on the fly
4. **Protect** — mounts JWT auth middleware, creates `_users` table for authentication
5. **Serve** — starts uvicorn with connection pooling, metrics, and health checks

No ORM models to write. No migrations to run. No code to generate.

## License

[MIT](LICENSE)
