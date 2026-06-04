import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dbzap.server.metrics import MetricsCollector

_EXCLUDED_PREFIXES = ("/healthz",)
_FALLBACK_CACHE_MAX = 1024
_UNMATCHED_LABEL = "/__unmatched__"


class PerformanceMiddleware:
    """Pure ASGI middleware that times every request and feeds the metrics collector.

    Implemented as a plain ASGI 3 callable — NOT as
    ``starlette.middleware.base.BaseHTTPMiddleware``. The latter
    materializes responses into a ``StreamingResponse`` and bridges
    send/receive across an extra task; Starlette's own docs warn it
    adds significant overhead, often 30-50% on small JSON responses.
    Since dbzap is performance-sensitive (see ``specs/10-performance.md``)
    the timing layer must not re-buffer the body. See P0-6.

    Path label resolution (per ``specs/10-performance.md``):
      1. ``scope["route"]`` — populated by Starlette's router during
         the inner-app call. O(1) and used for the vast majority of
         requests, so the result is **not** cached (caching by raw path
         would let high-cardinality IDs swamp the cache key space).
      2. Fallback: scan ``app.routes`` and call ``route.matches(scope)``.
         Cached by the matched template (NOT raw path) since the only
         thing worth memoizing is the route object that won.
      3. Last resort: the raw URL path (unmatched 404). Never cached.

    ``in_progress`` MUST be decremented exactly once per increment, even
    if the inner app or ``record_request`` itself raises. Enforced via
    ``try/finally``.
    """

    def __init__(self, app: ASGIApp, collector: MetricsCollector) -> None:
        self.app = app
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
            return _UNMATCHED_LABEL

        for route in routes:
            try:
                match, _ = route.matches(request.scope)
            except Exception:
                continue
            if match == Match.FULL:
                fallback_path = getattr(route, "path", raw)
                if not isinstance(fallback_path, str):
                    fallback_path = raw
                self._cache_fallback((method, fallback_path), fallback_path)
                return fallback_path

        # 3) Last resort: collapse to a single sentinel label so unmatched
        # 404 traffic cannot inflate metric cardinality (P0-8 / spec 10).
        # Returning ``raw`` here lets ``GET /spam-${random}`` create a
        # distinct metrics entry per request — an easy DoS / OOM vector.
        return _UNMATCHED_LABEL

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # ASGI middleware is asked to handle every protocol — pass through
        # anything we don't measure (lifespan, websockets) untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(p) for p in _EXCLUDED_PREFIXES):
            await self.app(scope, receive, send)
            return

        self._collector.increment_in_progress()
        start = time.monotonic()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            try:
                # Build a Request to reuse the existing _resolve_route logic.
                # ``scope["route"]`` has already been populated by the router.
                request = Request(scope)
                route = self._resolve_route(request)
                self._collector.record_request(
                    scope.get("method", "GET"), route, status_code, duration
                )
            finally:
                # MUST decrement even if record_request raises. Otherwise
                # the gauge drifts forever.
                self._collector.decrement_in_progress()


# Backwards-compat names exported from the previous (BaseHTTPMiddleware)
# implementation. Kept only so callers that did
# ``from dbzap.server.middleware import RequestResponseEndpoint`` still
# import; intentionally typed as Any rather than re-importing the
# starlette internals we no longer need.
RequestResponseEndpoint = Callable[[Request], Awaitable[Any]]
