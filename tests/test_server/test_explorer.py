import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dbzap.core.config import Settings
from dbzap.server.app import create_app


def _settings(**kwargs) -> Settings:  # type: ignore[no-untyped-def]
    defaults = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret_key": "test-explorer-secret",
        "explorer_username": None,
        "explorer_password": None,
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
async def app() -> FastAPI:
    return await create_app(settings=_settings(enable_explorer=True))


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def app_disabled() -> FastAPI:
    return await create_app(settings=_settings(enable_explorer=False))


@pytest.fixture
async def client_disabled(app_disabled: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app_disabled), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# /explorer
# ---------------------------------------------------------------------------


async def test_explorer_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/explorer")
    assert resp.status_code == 200


async def test_explorer_content_type_html(client: AsyncClient) -> None:
    resp = await client.get("/explorer")
    assert "text/html" in resp.headers["content-type"]


async def test_explorer_contains_app_root(client: AsyncClient) -> None:
    resp = await client.get("/explorer")
    assert "dbzap" in resp.text.lower()


async def test_explorer_contains_script_tag(client: AsyncClient) -> None:
    resp = await client.get("/explorer")
    assert "<script" in resp.text


async def test_explorer_disabled_returns_404(client_disabled: AsyncClient) -> None:
    resp = await client_disabled.get("/explorer")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------


async def test_static_css_served(client: AsyncClient) -> None:
    resp = await client.get("/explorer/static/css/style.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


async def test_static_js_served(client: AsyncClient) -> None:
    resp = await client.get("/explorer/static/js/app.js")
    assert resp.status_code == 200


async def test_static_disabled_returns_404(client_disabled: AsyncClient) -> None:
    resp = await client_disabled.get("/explorer/static/css/style.css")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# No CDN / external links in HTML
# ---------------------------------------------------------------------------


async def test_no_external_cdn_in_html(client: AsyncClient) -> None:
    resp = await client.get("/explorer")
    html = resp.text
    assert "cdn." not in html
    assert "unpkg.com" not in html
    assert "jsdelivr" not in html
    assert "googleapis.com" not in html


# ---------------------------------------------------------------------------
# /explorer/config
# ---------------------------------------------------------------------------


async def test_config_defaults_null(client: AsyncClient) -> None:
    resp = await client.get("/explorer/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"username": None, "password": None}


async def test_config_returns_configured_values() -> None:
    app = await create_app(
        settings=_settings(
            enable_explorer=True,
            explorer_username="admin",
            explorer_password="secret",
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/explorer/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"username": "admin", "password": "secret"}


async def test_config_disabled_returns_404(client_disabled: AsyncClient) -> None:
    resp = await client_disabled.get("/explorer/config")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Theme toggle in HTML
# ---------------------------------------------------------------------------


async def test_theme_toggle_button_present(client: AsyncClient) -> None:
    resp = await client.get("/explorer")
    html = resp.text
    assert 'id="theme-btn"' in html


async def test_theme_inline_script_present(client: AsyncClient) -> None:
    resp = await client.get("/explorer")
    html = resp.text
    assert "dbzap-theme" in html
