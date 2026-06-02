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

```bash
# 1. Install
pip install dbzap        # or: poetry add dbzap

# 2. Configure
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost/mydb"
export JWT_SECRET_KEY="pick-a-strong-secret"

# 3. Run
dbzap serve
```

That's it. Every table in your database now has full CRUD endpoints.

- **REST**: `http://localhost:8000/docs` — Swagger UI
- **GraphQL**: `http://localhost:8000/graphql` — GraphiQL
- **Explorer**: `http://localhost:8000/explorer` — API testing + dashboard
- **Health**: `http://localhost:8000/healthz`

## What You Get

| Feature | Details |
|---------|---------|
| REST CRUD | `POST / GET / PUT / PATCH / DELETE` per table, auto-generated Pydantic models |
| GraphQL | Query + Mutation per table, auto-generated types |
| Auth | JWT login/register, all endpoints protected by default |
| API Explorer | Built-in UI to browse, test, and debug your APIs |
| Dashboard | Real-time metrics: request rate, latency, DB pool health |
| Health Check | `/healthz` liveness + readiness probes for K8s |
| Metrics | Prometheus-compatible `/metrics` endpoint |

## Configuration

All settings via environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | *required* | `postgresql+asyncpg://...` |
| `JWT_SECRET_KEY` | *required* | HMAC secret for JWT signing |
| `API_MODE` | `both` | `rest`, `graphql`, or `both` |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DB_POOL_SIZE` | `10` | Connection pool size |
| `ENABLE_EXPLORER` | `true` | Enable/disable the web UI |

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

MIT
