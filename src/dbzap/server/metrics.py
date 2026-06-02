import math
import threading
from collections import defaultdict
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, math.inf)


class MetricsCollector:
    """Thread-safe, async-safe in-memory metrics store. No external deps."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # counters: (method, path, status) -> int
        self._request_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        # histogram sums: (method, path) -> float
        self._duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        # histogram counts: (method, path) -> int
        self._duration_count: dict[tuple[str, str], int] = defaultdict(int)
        # histogram buckets: (method, path, le) -> int
        self._duration_buckets: dict[tuple[str, str, float], int] = defaultdict(int)

        # db query histograms: (table, operation) -> sum/count/buckets
        self._db_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._db_count: dict[tuple[str, str], int] = defaultdict(int)
        self._db_buckets: dict[tuple[str, str, float], int] = defaultdict(int)

        # gauges
        self._in_progress: int = 0
        self._pool_size: int = 0
        self._pool_checked_out: int = 0
        self._pool_overflow: int = 0

    def record_request(self, method: str, path: str, status: int, duration: float) -> None:
        with self._lock:
            key = (method, path, str(status))
            self._request_counts[key] += 1
            hkey = (method, path)
            self._duration_sum[hkey] += duration
            self._duration_count[hkey] += 1
            for b in _BUCKETS:
                if duration <= b:
                    self._duration_buckets[(method, path, b)] += 1

    def set_in_progress(self, count: int) -> None:
        with self._lock:
            self._in_progress = count

    def increment_in_progress(self) -> None:
        with self._lock:
            self._in_progress += 1

    def decrement_in_progress(self) -> None:
        with self._lock:
            self._in_progress = max(0, self._in_progress - 1)

    def record_db_query(self, table: str, operation: str, duration: float) -> None:
        with self._lock:
            hkey = (table, operation)
            self._db_sum[hkey] += duration
            self._db_count[hkey] += 1
            for b in _BUCKETS:
                if duration <= b:
                    self._db_buckets[(table, operation, b)] += 1

    def update_pool_stats(self, pool_size: int, checked_out: int, overflow: int) -> None:
        with self._lock:
            self._pool_size = pool_size
            self._pool_checked_out = checked_out
            self._pool_overflow = overflow

    def export_prometheus(self) -> str:
        with self._lock:
            lines: list[str] = []

            # http_requests_total
            lines += [
                "# HELP http_requests_total Total number of HTTP requests",
                "# TYPE http_requests_total counter",
            ]
            for (method, path, status), count in sorted(self._request_counts.items()):
                lines.append(
                    f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}'
                )

            # http_request_duration_seconds
            lines += [
                "",
                "# HELP http_request_duration_seconds HTTP request duration in seconds",
                "# TYPE http_request_duration_seconds histogram",
            ]
            seen_hkeys: set[tuple[str, str]] = set()
            for (method, path, le), cnt in sorted(
                self._duration_buckets.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])
            ):
                le_str = "+Inf" if math.isinf(le) else str(le)
                lines.append(
                    f'http_request_duration_seconds_bucket{{method="{method}",path="{path}",le="{le_str}"}} {cnt}'
                )
                seen_hkeys.add((method, path))
            for hkey in sorted(seen_hkeys):
                method, path = hkey
                lines.append(
                    f'http_request_duration_seconds_count{{method="{method}",path="{path}"}} {self._duration_count[hkey]}'
                )
                lines.append(
                    f'http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {round(self._duration_sum[hkey], 6)}'
                )

            # http_requests_in_progress
            lines += [
                "",
                "# HELP http_requests_in_progress Number of requests currently being processed",
                "# TYPE http_requests_in_progress gauge",
                f"http_requests_in_progress {self._in_progress}",
            ]

            # db_pool_*
            lines += [
                "",
                "# HELP db_pool_size Database connection pool size",
                "# TYPE db_pool_size gauge",
                f"db_pool_size {self._pool_size}",
                "",
                "# HELP db_pool_checked_out Number of connections currently checked out from pool",
                "# TYPE db_pool_checked_out gauge",
                f"db_pool_checked_out {self._pool_checked_out}",
                "",
                "# HELP db_pool_overflow Number of overflow connections",
                "# TYPE db_pool_overflow gauge",
                f"db_pool_overflow {self._pool_overflow}",
            ]

            # db_query_duration_seconds
            lines += [
                "",
                "# HELP db_query_duration_seconds Database query duration in seconds",
                "# TYPE db_query_duration_seconds histogram",
            ]
            seen_db_keys: set[tuple[str, str]] = set()
            for (table, operation, le), cnt in sorted(
                self._db_buckets.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])
            ):
                le_str = "+Inf" if math.isinf(le) else str(le)
                lines.append(
                    f'db_query_duration_seconds_bucket{{table="{table}",operation="{operation}",le="{le_str}"}} {cnt}'
                )
                seen_db_keys.add((table, operation))
            for dkey in sorted(seen_db_keys):
                table, operation = dkey
                lines.append(
                    f'db_query_duration_seconds_count{{table="{table}",operation="{operation}"}} {self._db_count[dkey]}'
                )
                lines.append(
                    f'db_query_duration_seconds_sum{{table="{table}",operation="{operation}"}} {round(self._db_sum[dkey], 6)}'
                )

            lines.append("")
            return "\n".join(lines)


def create_metrics_router(collector: MetricsCollector) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(collector.export_prometheus())

    return router
