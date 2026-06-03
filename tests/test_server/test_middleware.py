import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dbzap.core.config import Settings
from dbzap.server.app import create_app


def _settings(**kwargs) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-perf-secret",
        **kwargs,
    )


@pytest.fixture
async def app() -> FastAPI:
    return await create_app(settings=_settings())


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


async def test_metrics_endpoint_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200


async def test_metrics_content_type_text(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


async def test_metrics_no_auth_required(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200


async def test_metrics_contains_prometheus_structure(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    text = resp.text
    assert "# HELP" in text
    assert "# TYPE" in text
    assert "http_requests_total" in text
    assert "http_requests_in_progress" in text


async def test_request_recorded_in_metrics(client: AsyncClient) -> None:
    await client.get("/healthz")
    resp = await client.get("/metrics")
    # /healthz should be excluded from metrics
    assert "/healthz" not in resp.text


async def test_auth_request_recorded(client: AsyncClient) -> None:
    await client.post("/auth/login", json={"username": "nobody", "password": "wrong"})
    resp = await client.get("/metrics")
    text = resp.text
    assert "/auth/login" in text


async def test_in_progress_gauge_present(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert "http_requests_in_progress" in resp.text


async def test_pool_stats_in_metrics(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    text = resp.text
    assert "db_pool_size" in text
    assert "db_pool_checked_out" in text
    assert "db_pool_overflow" in text


# ---------------------------------------------------------------------------
# GZip compression is wired
# ---------------------------------------------------------------------------


async def test_gzip_accepted(client: AsyncClient) -> None:
    resp = await client.get("/metrics", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PerformanceMiddleware unit tests
# Regression: route cache & in_progress decrement under exception.
# ---------------------------------------------------------------------------


def _http_scope(method: str, path: str, *, route: object | None = None) -> dict:
    """Build a minimal Starlette HTTP scope for direct middleware unit tests."""

    class _StubApp:
        routes: list = []

    scope: dict = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1234),
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "root_path": "",
        "app": _StubApp(),
    }
    if route is not None:
        scope["route"] = route
    return scope


def test_route_cache_does_not_store_per_id_entries() -> None:
    """Bug 2: cache MUST NOT grow with every concrete ID under /api/users/{pk}.

    Before the fix, ``(method, raw_path)`` was the cache key — so each of
    /api/users/1, /api/users/2, ... took a distinct slot, hitting the
    1024 cap and oscillating. After the fix, scope['route'] hits return
    O(1) without polluting any per-raw-path cache.
    """
    from starlette.requests import Request

    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    class FakeRoute:
        path = "/api/users/{pk}"

    class FakeApp:
        routes: list = []

    fake_route = FakeRoute()

    async def _app(scope, receive, send):  # pragma: no cover - never called
        pass

    mw = PerformanceMiddleware(app=_app, collector=MetricsCollector())

    # Hit 200 distinct concrete IDs.
    for i in range(200):
        scope = _http_scope("GET", f"/api/users/{i}", route=fake_route)
        request = Request(scope)
        resolved = mw._resolve_route(request)
        assert resolved == "/api/users/{pk}"

    # Whatever caching strategy is used, it MUST NOT hold one entry per
    # concrete ID. A single template entry (or zero) is acceptable; 200 is not.
    assert len(mw._route_cache) <= 1, (
        f"route cache grew to {len(mw._route_cache)} entries — high-cardinality "
        "IDs are leaking into the cache key"
    )


async def test_in_progress_decrements_when_record_request_raises() -> None:
    """Bug 3: in_progress gauge MUST drop back even if record_request raises.

    Without try/finally, a raise inside record_request would skip the
    decrement and the gauge would drift up monotonically.
    """
    from starlette.requests import Request
    from starlette.responses import Response

    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    class BoomCollector(MetricsCollector):
        def record_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("collector boom")

    collector = BoomCollector()
    assert collector._in_progress == 0

    async def _app(scope, receive, send):  # pragma: no cover
        pass

    mw = PerformanceMiddleware(app=_app, collector=collector)

    async def call_next(request: Request) -> Response:
        return Response(b"ok")

    scope = _http_scope("GET", "/api/anything")
    request = Request(scope)

    with pytest.raises(RuntimeError, match="collector boom"):
        await mw.dispatch(request, call_next)

    assert collector._in_progress == 0, (
        "in_progress leaked when record_request raised — missing try/finally"
    )


async def test_in_progress_decrements_when_call_next_raises() -> None:
    """Bug 3 (companion): exception in downstream handler must still decrement."""
    from starlette.requests import Request

    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    collector = MetricsCollector()

    async def _app(scope, receive, send):  # pragma: no cover
        pass

    mw = PerformanceMiddleware(app=_app, collector=collector)

    async def call_next(request: Request):
        raise ValueError("downstream blew up")

    scope = _http_scope("GET", "/api/anything")
    request = Request(scope)

    with pytest.raises(ValueError, match="downstream blew up"):
        await mw.dispatch(request, call_next)

    assert collector._in_progress == 0
