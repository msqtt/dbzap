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

    Exercised against the pure-ASGI ``__call__`` path (P0-6 / spec 10).
    """
    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    class BoomCollector(MetricsCollector):
        def record_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("collector boom")

    collector = BoomCollector()
    assert collector._in_progress == 0

    async def inner_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = PerformanceMiddleware(app=inner_app, collector=collector)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_msg: dict) -> None:
        pass

    scope = _http_scope("GET", "/api/anything")
    with pytest.raises(RuntimeError, match="collector boom"):
        await mw(scope, receive, send)

    assert collector._in_progress == 0, (
        "in_progress leaked when record_request raised — missing try/finally"
    )


async def test_in_progress_decrements_when_call_next_raises() -> None:
    """Bug 3 (companion): exception in downstream handler must still decrement.

    Exercised against the pure-ASGI ``__call__`` path.
    """
    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    collector = MetricsCollector()

    async def inner_app(scope, receive, send):
        raise ValueError("downstream blew up")

    mw = PerformanceMiddleware(app=inner_app, collector=collector)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_msg: dict) -> None:
        pass

    scope = _http_scope("GET", "/api/anything")
    with pytest.raises(ValueError, match="downstream blew up"):
        await mw(scope, receive, send)

    assert collector._in_progress == 0


def test_middleware_is_pure_asgi_not_basehttpmiddleware() -> None:
    """P0-6 / spec 10: PerformanceMiddleware MUST be a plain ASGI 3
    callable. Inheriting BaseHTTPMiddleware re-materializes responses
    through a StreamingResponse and adds 30-50% overhead on small JSON
    responses — exactly the workload dbzap optimizes.
    """
    from starlette.middleware.base import BaseHTTPMiddleware

    from dbzap.server.middleware import PerformanceMiddleware

    assert not issubclass(PerformanceMiddleware, BaseHTTPMiddleware), (
        "PerformanceMiddleware still inherits BaseHTTPMiddleware — "
        "switch to pure ASGI to avoid response re-buffering"
    )
    # Sanity: must be invocable as ASGI (i.e. expose async __call__).
    import inspect

    assert callable(PerformanceMiddleware)
    assert inspect.iscoroutinefunction(PerformanceMiddleware.__call__)


async def test_middleware_passes_response_body_unchanged() -> None:
    """Pure-ASGI implementation must NOT re-buffer the body — bytes go
    through verbatim, in the same number of messages, with no
    ``StreamingResponse`` wrapping.
    """
    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    sent: list[dict] = []

    async def inner_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 201, "headers": [(b"x-custom", b"yes")]})
        await send({"type": "http.response.body", "body": b"hello", "more_body": True})
        await send({"type": "http.response.body", "body": b"-world", "more_body": False})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: dict) -> None:
        sent.append(msg)

    collector = MetricsCollector()
    mw = PerformanceMiddleware(app=inner_app, collector=collector)
    await mw(scope=_http_scope("POST", "/api/anything"), receive=receive, send=send)

    # 1 start + 2 body messages, in order, with bodies preserved.
    assert [m["type"] for m in sent] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]
    assert sent[0]["status"] == 201
    assert sent[0]["headers"] == [(b"x-custom", b"yes")]
    assert sent[1]["body"] == b"hello"
    assert sent[1]["more_body"] is True
    assert sent[2]["body"] == b"-world"
    assert sent[2]["more_body"] is False


# ---------------------------------------------------------------------------
# P0-8: unmatched URLs MUST collapse to a single sentinel label,
# otherwise random 404 traffic explodes metrics cardinality.
# ---------------------------------------------------------------------------


def test_unmatched_path_collapses_to_sentinel_label() -> None:
    """A raw URL with no matching route MUST resolve to ``/__unmatched__``,
    not to the request path. Otherwise an attacker can hit
    ``/spam-${random}`` in a loop and balloon the metrics dict.
    """
    from starlette.requests import Request

    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    async def _app(scope, receive, send):  # pragma: no cover
        pass

    mw = PerformanceMiddleware(app=_app, collector=MetricsCollector())

    for i in range(50):
        scope = _http_scope("GET", f"/random-junk-{i}")
        # No scope["route"], no matching app.routes — fallback fires.
        request = Request(scope)
        resolved = mw._resolve_route(request)
        assert resolved == "/__unmatched__", (
            f"unmatched URL {scope['path']} resolved to {resolved!r} — "
            "raw URLs leak through to metrics labels and explode cardinality"
        )


async def test_unmatched_paths_dont_explode_metrics_cardinality() -> None:
    """Integration: a flood of distinct unmatched URLs must produce ONE
    metrics entry, not N. Goes end-to-end through the ASGI ``__call__``.
    """
    from dbzap.server.metrics import MetricsCollector
    from dbzap.server.middleware import PerformanceMiddleware

    collector = MetricsCollector()

    async def inner_app(scope, receive, send):
        # Pretend every request 404s.
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = PerformanceMiddleware(app=inner_app, collector=collector)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_msg: dict) -> None:
        pass

    for i in range(50):
        await mw(scope=_http_scope("GET", f"/spam-{i}"), receive=receive, send=send)

    paths = {key[1] for key in collector._request_counts}
    assert paths == {"/__unmatched__"}, (
        f"expected only the sentinel label, got {sorted(paths)} — high-cardinality "
        "leak into metrics"
    )
    # And only one duration-bucket entry per (method, sentinel, le).
    sentinel_buckets = [
        key for key in collector._duration_buckets if key[1] == "/__unmatched__"
    ]
    assert len(sentinel_buckets) == 9  # one per histogram bucket boundary
