# Feature: Configuration Management

## Goal
Provide a type-safe, environment-variable-driven configuration system for the entire application.

## Scope
- In scope: Database connection string, JWT settings, server host/port, API mode selection, loading from `.env` file
- Out of scope: Runtime config hot-reload, config file formats (YAML/TOML), multi-tenant config

## API Contract

No external API. Internal interface:

```python
class Settings(pydantic_settings.BaseSettings):
    # Database
    database_url: str                    # e.g. postgresql+asyncpg://...

    # Auth
    jwt_secret_key: str                  # HMAC secret
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # API mode: "rest" | "graphql" | "both"
    api_mode: Literal["rest", "graphql", "both"] = "both"

    # Explorer UI
    enable_explorer: bool = True

    # Database pool
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_statement_timeout: str = "5s"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
```

Singleton accessor:
```python
def get_settings() -> Settings: ...
```

## Data Model

No database tables. Config is read-only after startup.

## Edge Cases
- Missing `DATABASE_URL` must raise a clear validation error at startup, not a cryptic crash.
- Invalid `api_mode` value must be rejected with a list of valid options.
- `.env` file is optional; env vars take precedence over `.env` values.
- `jwt_secret_key` must not have a usable default - force explicit configuration.

## Acceptance Criteria
- [ ] `Settings` loads all fields from env vars and `.env` file.
- [ ] Missing required fields raise `pydantic.ValidationError` with descriptive messages.
- [ ] `get_settings()` returns a cached singleton instance.
- [ ] `jwt_secret_key` has no insecure default value.
- [ ] `api_mode` validates against `rest`, `graphql`, `both` only.

## Module Location
`src/dbzap/core/config.py`

## Dependencies
- `pydantic-settings`
