"""Async engine factory shared by the app and the introspector.

The factory is dialect-aware: pool sizing kwargs and statement-timeout
mechanisms differ across PostgreSQL, MySQL, and SQLite. Centralizing the
construction here ensures every engine in the project goes through the
same code path — see ``specs/10-performance.md`` for the contract.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbzap.core.config import Settings

_SYNC_TO_ASYNC_SCHEMES: dict[str, str] = {
    "mysql": "mysql+aiomysql",
    "postgres": "postgresql+asyncpg",
    "postgresql": "postgresql+asyncpg",
}


def normalize_db_url(url: str) -> str:
    """Rewrite a sync driver URL to its async counterpart.

    Leaves URLs that already specify an async driver (e.g.
    ``postgresql+asyncpg://``) unchanged.
    """
    for sync_scheme, async_scheme in _SYNC_TO_ASYNC_SCHEMES.items():
        prefix = f"{sync_scheme}://"
        if url.startswith(prefix):
            return async_scheme + url[len(sync_scheme):]
    return url


def parse_timeout_ms(value: str) -> int | None:
    """Parse a duration string like ``5s`` / ``500ms`` / ``5`` into milliseconds.

    Returns ``None`` for empty or invalid values so callers can skip the
    statement-timeout wiring entirely instead of passing nonsense to the driver.
    """
    if not value:
        return None
    v = value.strip().lower()
    try:
        if v.endswith("ms"):
            return max(0, int(float(v[:-2].strip())))
        if v.endswith("s"):
            return max(0, int(float(v[:-1].strip()) * 1000))
        return max(0, int(float(v) * 1000))
    except (ValueError, TypeError):
        return None


def build_engine(cfg: Settings) -> AsyncEngine:
    """Build the async engine with dialect-aware pool sizing and timeouts.

    SQLite uses ``StaticPool`` and rejects ``pool_size``/``max_overflow``-style
    kwargs (raises ``TypeError: Invalid argument(s) sent to create_engine()``),
    so they are only forwarded for server databases.
    """
    url = normalize_db_url(cfg.database_url)
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    timeout_ms = parse_timeout_ms(cfg.db_statement_timeout)

    if url.startswith("postgresql"):
        kwargs["pool_size"] = cfg.db_pool_size
        kwargs["max_overflow"] = cfg.db_max_overflow
        kwargs["pool_timeout"] = cfg.db_pool_timeout
        kwargs["pool_recycle"] = cfg.db_pool_recycle
        if timeout_ms:
            # asyncpg honors ``server_settings`` to apply per-connection GUCs.
            kwargs["connect_args"] = {
                "server_settings": {"statement_timeout": str(timeout_ms)}
            }
    elif url.startswith("mysql"):
        kwargs["pool_size"] = cfg.db_pool_size
        kwargs["max_overflow"] = cfg.db_max_overflow
        kwargs["pool_timeout"] = cfg.db_pool_timeout
        kwargs["pool_recycle"] = cfg.db_pool_recycle
        if timeout_ms:
            # aiomysql executes ``init_command`` on every new connection.
            kwargs["connect_args"] = {
                "init_command": f"SET SESSION MAX_EXECUTION_TIME={timeout_ms}"
            }
    # SQLite: keep the default StaticPool; pool sizing flags would raise.

    return create_async_engine(url, **kwargs)
