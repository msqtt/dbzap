import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import Match

from dbzap.server.metrics import MetricsCollector

_EXCLUDED_PREFIXES = ("/healthz",)
_ROUTE_CACHE_MAX = 1024


class PerformanceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, collector: MetricsCollector) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._collector = collector
        # (method, raw_path) -> matched route template.  Bounded to keep
        # high-cardinality paths from leaking memory; evicted in bulk on cap.
        self._route_cache: dict[tuple[str, str], str] = {}

    def _cache_route(self, key: tuple[str, str], value: str) -> None:
        if len(self._route_cache) >= _ROUTE_CACHE_MAX:
            # Cheap eviction: clearing in bulk is O(n) once per ~1k unique keys.
            self._route_cache.clear()
        self._route_cache[key] = value

    def _resolve_route(self, request: Request) -> str:
        raw = request.url.path
        key = (request.method, raw)
        cached = self._route_cache.get(key)
        if cached is not None:
            return cached

        # 1) The downstream router writes the matched route into scope.  This
        #    avoids walking every registered route on each request — important
        #    because dbzap auto-generates one route group per table.
        scope_route = request.scope.get("route")
        if scope_route is not None:
            path_attr = getattr(scope_route, "path", None) or getattr(
                scope_route, "path_format", None
            )
            if isinstance(path_attr, str) and path_attr:
                self._cache_route(key, path_attr)
                return path_attr

        # 2) Fallback: full scan (e.g. sub-mount routers, unmatched paths).
        for route in request.app.routes:
            try:
                match, _ = route.matches(request.scope)
            except Exception:  # noqa: BLE001
                continue
            if match == Match.FULL:
                fallback_path = getattr(route, "path", raw)
                if not isinstance(fallback_path, str):
                    fallback_path = raw
                self._cache_route(key, fallback_path)
                return fallback_path

        # 3) Last resort: the raw URL path (do not cache — could be unbounded).
        return raw

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _EXCLUDED_PREFIXES):
            return await call_next(request)

        self._collector.increment_in_progress()
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.monotonic() - start
            route = self._resolve_route(request)
            self._collector.record_request(request.method, route, 500, duration)
            self._collector.decrement_in_progress()
            raise
        duration = time.monotonic() - start
        route = self._resolve_route(request)
        self._collector.record_request(
            request.method, route, response.status_code, duration
        )
        self._collector.decrement_in_progress()
        return response
