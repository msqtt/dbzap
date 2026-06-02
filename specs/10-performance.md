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


class PerformanceMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that times every request and feeds MetricsCollector."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        self.collector.set_in_progress(...)  # increment
        response = await call_next(request)
        duration = time.monotonic() - start
        self.collector.record_request(request.method, request.url.path, response.status_code, duration)
        self.collector.set_in_progress(...)  # decrement
        return response
```

## Performance Optimizations

### 1. Database Connection Pool

```python
# In create_app()
engine = create_async_engine(
    settings.database_url,
    pool_size=10,           # steady-state connections
    max_overflow=20,        # burst capacity
    pool_timeout=30,        # seconds to wait for a connection
    pool_recycle=1800,      # recycle connections every 30 min (avoid stale)
    pool_pre_ping=True,     # verify connection before use
)
```

Configurable via env:
```
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
```

### 2. Query Timeout

All generated queries enforce a server-side statement timeout:

```python
# Set per-session
await conn.execute(text("SET statement_timeout = '5s'"))
```

Configurable: `DB_STATEMENT_TIMEOUT=5s`

### 3. Response Compression

Enable gzip compression for responses > 1KB:

```python
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)
```

### 4. Schema Cache

Introspected schema is cached after first load. No redundant DB round-trips for schema info.

### 5. Connection Reuse

All CRUD operations within a single request share one connection from the pool (via `AsyncSession`), not one connection per query.

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
- [ ] `GET /metrics` returns Prometheus-compatible text format.
- [ ] Path labels are normalized to route patterns (no high cardinality).
- [ ] `http_requests_in_progress` gauge increments/decrements correctly, including on errors.
- [ ] Database pool stats (size, checked_out, overflow) are updated on each export.
- [ ] `db_query_duration_seconds` histogram tracks per-table query performance.
- [ ] Connection pool is configured with `pool_size`, `max_overflow`, `pool_recycle`, `pool_pre_ping`.
- [ ] Query timeout is enforced server-side (`statement_timeout`).
- [ ] GZip compression is enabled for responses > 1KB.
- [ ] `/healthz*` endpoints are excluded from metrics collection.
- [ ] Metrics are thread-safe and async-safe.
- [ ] No external dependency for metrics (no `prometheus_client` - implement text format directly).
- [ ] All pool and timeout settings are configurable via environment variables.

## Module Location
- `src/dbzap/server/metrics.py` - `MetricsCollector` and `/metrics` route
- `src/dbzap/server/middleware.py` - `PerformanceMiddleware`
- Pool config: `src/dbzap/core/config.py` (add `db_pool_size`, `db_max_overflow`, etc.)

## Dependencies
- `fastapi` (middleware, routes)
- `sqlalchemy[asyncio]` (pool stats, statement timeout)
- Standard library only for metrics storage (no `prometheus_client`)
