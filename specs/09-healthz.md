# Feature: Health Check Endpoint (/healthz)

## Goal
Provide a lightweight `/healthz` endpoint for liveness and readiness probes, enabling container orchestrators and load balancers to detect service health.

## Scope
- In scope: Liveness probe (process alive), readiness probe (database reachable), detailed health report (optional, behind auth), startup probe
- Out of scope: Dependency health for external services, circuit breaker integration, custom health indicators per table

## API Contract

### GET /healthz (Liveness)

Always public, no auth required.

Response (200):
```json
{
  "status": "ok",
  "timestamp": "2026-06-02T10:00:00Z",
  "uptime_seconds": 3600.5
}
```

This endpoint returns 200 as long as the process is running. It does NOT check database connectivity. Suitable for Kubernetes `livenessProbe`.

### GET /healthz/ready (Readiness)

Always public, no auth required.

Response (200) - healthy:
```json
{
  "status": "ok",
  "timestamp": "2026-06-02T10:00:00Z",
  "uptime_seconds": 3600.5,
  "checks": {
    "database": {
      "status": "ok",
      "latency_ms": 2.3
    }
  }
}
```

Response (503) - unhealthy:
```json
{
  "status": "error",
  "timestamp": "2026-06-02T10:00:00Z",
  "uptime_seconds": 3600.5,
  "checks": {
    "database": {
      "status": "error",
      "error": "Connection refused"
    }
  }
}
```

Readiness checks database connectivity with `SELECT 1`. Suitable for Kubernetes `readinessProbe`.

### GET /healthz/detail (Detailed)

Requires auth. Returns extended diagnostics.

Response (200):
```json
{
  "status": "ok",
  "timestamp": "2026-06-02T10:00:00Z",
  "uptime_seconds": 3600.5,
  "version": "0.1.0",
  "api_mode": "both",
  "checks": {
    "database": {
      "status": "ok",
      "latency_ms": 2.3,
      "pool_size": 5,
      "pool_checked_out": 2,
      "pool_overflow": 0
    }
  },
  "introspection": {
    "table_count": 12,
    "last_reload": "2026-06-02T09:00:00Z"
  }
}
```

## Data Model

No tables. All data is runtime state.

## Implementation

```python
class HealthCheck:
    def __init__(self, engine: AsyncEngine, introspector: SchemaIntrospector, start_time: datetime):
        self._engine = engine
        self._introspector = introspector
        self._start_time = start_time

    async def liveness(self) -> dict:
        """Process alive check - always returns ok."""

    async def readiness(self) -> tuple[int, dict]:
        """Database reachable check - returns (status_code, body)."""

    async def detail(self) -> dict:
        """Full diagnostic report."""
```

Routes are registered unconditionally in `create_app()`, before auth middleware.

## Edge Cases
- Database temporarily unreachable: `/healthz` still returns 200 (liveness), `/healthz/ready` returns 503.
- Database check timeout: cap at 5 seconds, return 503 with timeout error message.
- Service just started (startup phase): `/healthz/ready` returns 503 until first introspection completes.
- Connection pool exhausted: readiness check uses a fresh connection, not from the pool, to avoid deadlock.
- Clock skew: `uptime_seconds` uses `time.monotonic()` (not wall clock) to avoid drift issues.

## Acceptance Criteria
- [ ] `GET /healthz` returns 200 as long as the process is running.
- [ ] `GET /healthz/ready` returns 200 when database is reachable, 503 when not.
- [ ] `GET /healthz/detail` requires authentication and returns extended diagnostics.
- [ ] Response includes `timestamp` (ISO 8601) and `uptime_seconds`.
- [ ] Readiness check completes within 5 seconds (timeout protection).
- [ ] Readiness check uses a fresh connection to avoid pool deadlock.
- [ ] Uptime is calculated using monotonic clock.
- [ ] All three endpoints are excluded from auth middleware.
- [ ] Response `Content-Type` is `application/json`.
- [ ] No logging of health check requests (avoid log spam under frequent polling).

## Module Location
`src/dbzap/server/health.py`

## Dependencies
- `fastapi` (routes)
- `sqlalchemy[asyncio]` (database check)
- `src/dbzap/core/introspector.py` (schema stats for detail endpoint)
- `src/dbzap/core/config.py` (version info)
