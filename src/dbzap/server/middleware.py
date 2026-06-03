import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import Match

from dbzap.server.metrics import MetricsCollector

_EXCLUDED_PREFIXES = ("/healthz",)
_FALLBACK_CACHE_MAX = 1024


class PerformanceMiddleware(BaseHTTPMiddleware):
    """Times every request and feeds the metrics collector.

    Path label resolution (per ``specs/10-performance.md``):
      1. ``request.scope["route"]`` — populated by Starlette's router during
         ``call_next``. O(1) and used for the vast majority of requests, so
         the result is **not** cached (caching by raw path would let
         high-cardinality IDs swamp the cache key space).
      2. Fallback: scan ``app.routes`` and call ``route.matches(scope)``.
         Cached by the matched template (NOT raw path) since the only thing
         worth memoizing is the route object that won — the lookup itself
         is the expensive part, not key construction.
      3. Last resort: the raw URL path (unmatched 404). Never cached.

    ``in_progress`` MUST be decremented exactly once per increment, even if
    ``record_request`` itself raises. This is enforced via ``try/finally``.
    """

    def __init__(self, app: object, collector: MetricsCollector) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._collector = collector
        # Cache used only by the fallback (full-scan) branch. Keyed by the
        # *resolved template* — guaranteed bounded by the number of routes.
        self._route_cache: dict[tuple[str, str], str] = {}

    def _cache_fallback(self, key: tuple[str, str], value: str) -> None:
        if len(self._route_cache) >= _FALLBACK_CACHE_MAX:
            self._route_cache.clear()
        self._route_cache[key] = value

    def _resolve_route(self, request: Request) -> str:
        # 1) Fast path: matched route written into scope by the router.
        scope_route = request.scope.get("route")
        if scope_route is not None:
            path_attr = getattr(scope_route, "path", None) or getattr(
                scope_route, "path_format", None
            )
            if isinstance(path_attr, str) and path_attr:
                return path_attr

        # 2) Fallback: full scan. Use (method, template) as the cache key so
        #    high-cardinality concrete IDs cannot create per-ID cache entries.
        raw = request.url.path
        method = request.method
        try:
            routes = request.app.routes
        except (KeyError, AttributeError):
            return raw

        for route in routes:
            try:
                match, _ = route.matches(request.scope)
            except Exception:  # noqa: BLE001
                continue
            if match == Match.FULL:
                fallback_path = getattr(route, "path", raw)
                if not isinstance(fallback_path, str):
                    fallback_path = raw
                self._cache_fallback((method, fallback_path), fallback_path)
                return fallback_path

        # 3) Last resort: raw URL path (unmatched). Do not cache.
        return raw

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _EXCLUDED_PREFIXES):
            return await call_next(request)

        self._collector.increment_in_progress()
        start = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.monotonic() - start
            try:
                route = self._resolve_route(request)
                self._collector.record_request(
                    request.method, route, status_code, duration
                )
            finally:
                # MUST decrement even if record_request raises (lock failure,
                # collector bug, etc.). Otherwise the gauge drifts forever.
                self._collector.decrement_in_progress()
