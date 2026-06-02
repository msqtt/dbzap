import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import Match

from dbzap.server.metrics import MetricsCollector

_EXCLUDED_PREFIXES = ("/healthz",)


def _route_path(request: Request) -> str:
    """Return the matched route pattern, falling back to the raw path."""
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


class PerformanceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, collector: MetricsCollector) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._collector = collector

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        excluded = any(path.startswith(p) for p in _EXCLUDED_PREFIXES)

        if excluded:
            return await call_next(request)

        self._collector.increment_in_progress()
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.monotonic() - start
            route = _route_path(request)
            self._collector.record_request(request.method, route, 500, duration)
            self._collector.decrement_in_progress()
            raise
        duration = time.monotonic() - start
        route = _route_path(request)
        self._collector.record_request(request.method, route, response.status_code, duration)
        self._collector.decrement_in_progress()
        return response
