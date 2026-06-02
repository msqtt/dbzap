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
