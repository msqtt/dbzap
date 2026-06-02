# Feature: Server Bootstrap

## Goal
Assemble all components into a runnable ASGI application: load config, connect to the database, introspect schema, generate APIs, mount auth, and start the server.

## Scope
- In scope: Application factory, lifespan management (startup/shutdown), API mode routing (rest/graphql/both), database engine lifecycle, CORS middleware, CLI entry point (`dbzap serve`)
- Out of scope: Multi-process workers (use uvicorn CLI for that), deployment configs (Docker, k8s), health check endpoint (future), graceful request draining

## API Contract

### CLI

```bash
# Start the server (uses config from env / .env)
poetry run dbzap serve

# Override port
DBZAP_PORT=9000 poetry run dbzap serve
```

### Application Factory

```python
async def create_app(settings: Settings | None = None) -> FastAPI:
    """
    Build and return a configured FastAPI application.

    Steps:
    1. Load settings (or use provided)
    2. Create async database engine
    3. Introspect database schema
    4. Initialize auth (create _users table if needed)
    5. Mount auth routes (/auth/*)
    6. Based on settings.api_mode:
       - "rest"    -> generate and mount REST CRUD routes
       - "graphql" -> generate and mount GraphQL endpoint
       - "both"    -> mount both
    7. Return the app
    """
```

### Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    engine = create_async_engine(settings.database_url)
    introspector = SchemaIntrospector(engine)
    tables = await introspector.introspect()
    # ... wire up generators ...
    yield app_state
    # Shutdown
    await engine.dispose()
```

### API Mode Routing

| `api_mode`  | REST (`/api/*`) | GraphQL (`/graphql`) |
| ----------- | --------------- | -------------------- |
| `rest`      | Mounted         | Not mounted          |
| `graphql`   | Not mounted     | Mounted              |
| `both`      | Mounted         | Mounted              |

Auth routes (`/auth/*`) are always mounted regardless of `api_mode`.

## Data Model

No new tables. Orchestrates existing modules.

## Edge Cases
- Database unreachable at startup: log error with masked URL, exit with code 1.
- `api_mode` is validated by Settings (pydantic), so invalid values are caught before reaching the factory.
- Empty database: generators handle gracefully (REST: no routes registered; GraphQL: placeholder query type).
- Port already in use: uvicorn raises `OSError`, propagated to CLI output.
- `_users` table must be excluded from introspection results before passing to generators.

## Acceptance Criteria
- [ ] `create_app()` returns a working FastAPI application.
- [ ] Database engine is created and disposed during lifespan.
- [ ] Schema introspection runs at startup; results are cached.
- [ ] Auth routes are always available.
- [ ] REST routes are mounted when `api_mode` is `rest` or `both`.
- [ ] GraphQL endpoint is mounted when `api_mode` is `graphql` or `both`.
- [ ] `_users` table is filtered out before passing schema to generators.
- [ ] Server starts and responds to requests via `uvicorn`.
- [ ] CORS middleware is enabled (configurable origins, default `*` for development).

## Module Location
`src/dbzap/server/`
- `server/app.py` - `create_app()` factory and lifespan
- `server/__main__.py` - CLI entry point (`python -m dbzap`)

## Dependencies
- `fastapi`
- `uvicorn`
- All `src/dbzap/core/`, `src/dbzap/generators/`, `src/dbzap/auth/` modules
