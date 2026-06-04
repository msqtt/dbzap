# Feature: Performance Monitoring & Optimization

## Goal
Monitor request performance in real time, expose metrics via an endpoint, and apply baseline optimizations to ensure the auto-generated API handles production traffic efficiently.

## Scope
- In scope: Request timing middleware, per-endpoint latency tracking (p50/p95/p99), active request counter, `/metrics` endpoint (Prometheus-compatible text format), database connection pool tuning, query timeout enforcement, response compression
- Out of scope: Distributed tracing (OpenTelemetry), APM agent integration (Datadog/New Relic), custom dashboards, alerting rules, load testing tooling

## API Contract

### GET /metrics

Always public (can be restricted via config in the future).

Response (200, `text/plain; charset=utf-8`):

```
# HELP http_requests_total Total number of HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="GET",path="/api/users",status="200"} 1523
http_requests_total{method="POST",path="/api/users",status="201"} 412

# HELP http_request_duration_seconds HTTP request duration in seconds
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.005"} 800
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.01"} 1200
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.025"} 1450
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.05"} 1500
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.1"} 1520
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.25"} 1523
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="0.5"} 1523
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="1.0"} 1523
http_request_duration_seconds_bucket{method="GET",path="/api/users",le="+Inf"} 1523
http_request_duration_seconds_count{method="GET",path="/api/users"} 1523
http_request_duration_seconds_sum{method="GET",path="/api/users"} 12.45

# HELP http_requests_in_progress Number of requests currently being processed
# TYPE http_requests_in_progress gauge
http_requests_in_progress 3

# HELP db_pool_size Database connection pool size
# TYPE db_pool_size gauge
db_pool_size 10

# HELP db_pool_checked_out Number of connections currently checked out from pool
# TYPE db_pool_checked_out gauge
db_pool_checked_out 3

# HELP db_pool_overflow Number of overflow connections
# TYPE db_pool_overflow gauge
db_pool_overflow 0

# HELP db_query_duration_seconds Database query duration in seconds
# TYPE db_query_duration_seconds histogram
db_query_duration_seconds_bucket{table="users",operation="SELECT",le="0.005"} 900
...
```

### Metrics Collected

| Metric | Type | Labels |
| ------ | ---- | ------ |
| `http_requests_total` | Counter | method, path, status |
| `http_request_duration_seconds` | Histogram | method, path |
| `http_requests_in_progress` | Gauge | - |
| `db_pool_size` | Gauge | - |
| `db_pool_checked_out` | Gauge | - |
| `db_pool_overflow` | Gauge | - |
| `db_query_duration_seconds` | Histogram | table, operation |
| `introspection_table_count` | Gauge | - |
| `introspection_last_reload_timestamp` | Gauge | - |

## Internal Interface

```python
class MetricsCollector:
    """Thread-safe, async-safe metrics storage. No external dependency (no prometheus_client)."""

    def record_request(self, method: str, path: str, status: int, duration: float) -> None:
        """Record a completed HTTP request."""

    def set_in_progress(self, count: int) -> None:
        """Update the in-progress gauge."""

    def record_db_query(self, table: str, operation: str, duration: float) -> None:
        """Record a database query execution."""

    def update_pool_stats(self, pool_size: int, checked_out: int, overflow: int) -> None:
        """Update database pool gauges."""

    def export_prometheus(self) -> str:
        """Render all metrics in Prometheus text exposition format."""


def create_metrics_router(
    collector: MetricsCollector,
    pool_stats_provider: Callable[[], tuple[int, int, int]] | None = None,
) -> APIRouter:
    """Build the ``/metrics`` router.

    ``pool_stats_provider`` is a zero-arg callable that returns
    ``(pool_size, checked_out, overflow)``.  When provided, the router
    refreshes the gauges on **every scrape** by calling the provider
    just before rendering.  This keeps pool metrics live without
    requiring background tasks or hooks at every checkout/checkin.
    The app factory wires the provider to the engine's pool:

        def _pool_stats() -> tuple[int, int, int]:
            pool = engine.pool
            return (pool.size(), pool.checkedout(), max(0, pool.overflow()))
        app.include_router(create_metrics_router(collector, pool_stats_provider=_pool_stats))


class PerformanceMiddleware:
    """Pure ASGI middleware that times every request and feeds MetricsCollector.

    MUST be implemented as a plain ASGI 3 callable (``async def __call__(
    scope, receive, send)``) — NOT as ``starlette.middleware.base.
    BaseHTTPMiddleware``. ``BaseHTTPMiddleware`` materializes responses
    into a ``StreamingResponse`` and bridges send/receive across an
    extra task; Starlette's own docs warn it is significantly slower
    (often 30-50% on small JSON responses) and breaks streaming for
    large responses. Since dbzap is performance-sensitive (see this
    spec) and uses GZipMiddleware downstream, the request-timing layer
    must not re-buffer the response.

    Implementation contract:
    * Wrap the inner app via ``await self.app(scope, receive, send_wrapper)``.
    * ``send_wrapper`` snoops ``http.response.start`` to capture the
      status code, then forwards the message untouched. Body messages
      pass through verbatim.
    * MUST guarantee that ``in_progress`` is decremented exactly once
      for every increment, including when the inner app or
      ``record_request`` itself raises. Use ``try/finally`` — never
      duplicate decrement calls in both the happy and exception
      branches.
    """

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # ... time, capture status from http.response.start, record metrics


### Route Resolution (path label normalization)

`PerformanceMiddleware` MUST normalize the `path` label to the matched
route template (e.g. `/api/users/{pk}`) — never the raw URL with the
concrete ID — to keep metric cardinality bounded.

Resolution order:

1. **Primary path**: read `request.scope["route"]` (set by Starlette's
   router during `call_next`). This is O(1) and is the path used in
   nearly all real requests.
2. **Fallback**: walk `app.routes` calling `route.matches(scope)` for
   sub-mounts or unmatched paths. Result MAY be cached by route
   template (NOT by raw path, because the raw path includes the
   high-cardinality ID and would defeat the bound).
3. **Last resort (no match found)**: collapse to a single sentinel
   label `"/__unmatched__"`. The raw URL MUST NOT be used here. An
   attacker can hit `/spam-${random}` in a tight loop — every distinct
   path would become a new entry in `MetricsCollector._request_counts`,
   `_duration_sum`, `_duration_count`, and `_duration_buckets`,
   exploding memory and slowing `export_prometheus()`. The sentinel
   keeps unmatched-404 traffic at O(1) cardinality regardless of the
   request volume.

A naive `(method, raw_path) → template` cache is a bug: every concrete
ID becomes a distinct key, the bound flips on/off in a tight loop, and
no cache hit ever happens for paths with high cardinality. Prefer not
caching the scope-route fast path at all.

## Performance Optimizations

### 1. Database Connection Pool (Dialect-Aware)

Pool sizing parameters (`pool_size`, `max_overflow`, `pool_timeout`,
`pool_recycle`) only apply to server databases (PostgreSQL / MySQL). SQLite
uses SQLAlchemy's `StaticPool` and **rejects** these kwargs with
`TypeError: Invalid argument(s) 'pool_size','max_overflow' sent to
create_engine()`. The engine factory MUST inspect the URL dialect and only
forward pool kwargs for the dialects that accept them.

```python
def _build_engine(cfg: Settings) -> AsyncEngine:
    url = _normalize_db_url(cfg.database_url)
    kwargs = {"pool_pre_ping": True}
    if url.startswith("postgresql") or url.startswith("mysql"):
        kwargs.update(
            pool_size=cfg.db_pool_size,
            max_overflow=cfg.db_max_overflow,
            pool_timeout=cfg.db_pool_timeout,
            pool_recycle=cfg.db_pool_recycle,
        )
    # SQLite: keep StaticPool defaults; pool sizing flags would raise.
    return create_async_engine(url, **kwargs)
```

This factory MUST be reused **everywhere an engine is constructed** —
notably both in `create_app()` and in `SchemaIntrospector.__init__`'s
no-engine fallback path. Forgetting one site breaks SQLite at startup.

Configurable via env:
```
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
```

### 2. Query Timeout (Dialect-Aware)

Statement timeout enforcement depends on the dialect:

| Dialect    | Mechanism |
|------------|-----------|
| PostgreSQL | `connect_args={"server_settings": {"statement_timeout": "<ms>"}}` (asyncpg) |
| MySQL      | `connect_args={"init_command": "SET SESSION MAX_EXECUTION_TIME=<ms>"}` (aiomysql) |
| SQLite     | not enforced (no driver-level support; rely on app-level timeouts) |

Configurable: `DB_STATEMENT_TIMEOUT=5s` (parsed as `5s` / `500ms` / bare seconds).

### 3. Response Compression

Enable gzip compression for responses > 1KB:

```python
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)
```

### 4. Schema Cache

Introspected schema is cached after first load. No redundant DB round-trips for schema info.

### 5. Connection Reuse (Single Connection Per Mutation)

dbzap does not use ORM `AsyncSession`. CRUD operations use SQLAlchemy
Core through `engine.connect()` / `engine.begin()`. Mutations that need
to return the affected row MUST share **one connection** for the
write + read-back, halving pool acquisitions per request:

```python
async with engine.connect() as conn:
    async with conn.begin():
        result = await conn.execute(insert(tbl).values(**data))
    # Same connection, after commit — read-back observes the inserted row.
    row = (await conn.execute(select(tbl).where(...))).mappings().first()
```

Wrapping the read-back in a separate `engine.connect()` call doubles
pool round-trips per mutation, which becomes a bottleneck under load.

### 6. Count Query Optimization (No Subquery Wrapping)

List endpoints' `total` count MUST NOT wrap the full `SELECT *` in a
subquery. Reuse the `WHERE` clause directly on the table so the planner
can satisfy the count from indexes:

```python
# BAD: forces full row materialization through a derived table
count_q = select(func.count()).select_from(base_q.subquery())

# GOOD: index-friendly count, same WHERE clauses
count_q = select(func.count()).select_from(sa_tbl)
if base_q.whereclause is not None:
    count_q = count_q.where(base_q.whereclause)
```

## Data Model

No tables. All metrics are in-memory. Metrics are reset on process restart.

## Edge Cases
- High cardinality paths (e.g. `/api/users/12345`): normalize path labels to `/api/users/{id}` to avoid unbounded metric growth. Use the route pattern from FastAPI, not the raw URL.
- `/metrics` under heavy load: export is a simple string render, no locking on read (use atomic counters).
- Metrics endpoint scraped every 10s by Prometheus: ensure export is fast (< 1ms).
- `PerformanceMiddleware` ordering: must be added before GZipMiddleware to measure uncompressed response time.
- Health check endpoints (`/healthz*`): excluded from metrics to avoid noise from frequent polling.
- Request timeout (client disconnect): still record the partial duration, do not leak the in-progress counter.
- Pool exhaustion: `pool_timeout` triggers 503, recorded as a normal request with 503 status.

## Acceptance Criteria
- [ ] `PerformanceMiddleware` records duration and status for every HTTP request.
- [ ] `PerformanceMiddleware` is implemented as a plain ASGI 3 callable, NOT as `starlette.middleware.base.BaseHTTPMiddleware`. The latter materializes responses into a `StreamingResponse` and adds 30-50% overhead on small responses (see Starlette docs); the timing layer must not re-buffer the body.
- [ ] `GET /metrics` returns Prometheus-compatible text format.
- [ ] Path labels are normalized to route patterns (no high cardinality).
- [ ] `PerformanceMiddleware` resolves the route via `scope["route"]` (O(1)) before falling back to scanning `app.routes`.
- [ ] `PerformanceMiddleware` does NOT cache by raw URL path — the cache, if any, MUST be keyed in a way that survives high-cardinality IDs.
- [ ] Unmatched paths (e.g. random URLs that return 404) are collapsed to a single sentinel label (`"/__unmatched__"`) before recording — never sent verbatim to `MetricsCollector` (would let attackers explode memory by hitting random URLs).
- [ ] `http_requests_in_progress` gauge increments/decrements correctly, including when `record_request` itself raises (use `try/finally`).
- [ ] Database pool stats (size, checked_out, overflow) are refreshed on every `/metrics` scrape via `pool_stats_provider`.
- [ ] `db_query_duration_seconds` histogram tracks per-table query performance.
- [ ] Connection pool is configured with `pool_size`, `max_overflow`, `pool_recycle`, `pool_pre_ping` for PostgreSQL and MySQL only.
- [ ] SQLite engine construction MUST NOT pass `pool_size`/`max_overflow`/`pool_timeout`/`pool_recycle` (would raise `TypeError`).
- [ ] The dialect-aware engine factory is reused by `SchemaIntrospector`'s no-engine fallback path.
- [ ] Query timeout is enforced server-side via dialect-appropriate connect_args (PG `server_settings`, MySQL `init_command`).
- [ ] GZip compression is enabled for responses > 1KB.
- [ ] `/healthz*` endpoints are excluded from metrics collection.
- [ ] Metrics are thread-safe and async-safe.
- [ ] No external dependency for metrics (no `prometheus_client` - implement text format directly).
- [ ] All pool and timeout settings are configurable via environment variables.
- [ ] Mutation routes (POST/PUT/PATCH) share a single connection for the write + read-back.

## Module Location
- `src/dbzap/server/metrics.py` - `MetricsCollector` and `/metrics` route
- `src/dbzap/server/middleware.py` - `PerformanceMiddleware`
- Pool config: `src/dbzap/core/config.py` (add `db_pool_size`, `db_max_overflow`, etc.)

## Dependencies
- `fastapi` (middleware, routes)
- `sqlalchemy[asyncio]` (pool stats, statement timeout)
- Standard library only for metrics storage (no `prometheus_client`)
