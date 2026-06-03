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
    jwt_secret_key: str                  # HMAC secret (required, no default)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    auth_mode: Literal["jwt", "basic", "both"] = "jwt"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS — secure-by-default. Wildcard origin requires credentials=False
    # (browsers reject `Access-Control-Allow-Origin: *` with credentials).
    cors_origins: list[str] = ["*"]
    cors_allow_credentials: bool = False

    # API mode: "rest" | "graphql" | "both"
    api_mode: Literal["rest", "graphql", "both"] = "both"

    # Explorer UI — admin user is seeded from these at startup. Without
    # them no admin user exists and authentication will always fail.
    enable_explorer: bool = True
    explorer_username: str | None = None
    explorer_password: str | None = None

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
- `cors_origins=["*"]` combined with `cors_allow_credentials=True` is invalid: the W3C CORS spec forbids it and modern browsers reject the response. The application MUST coerce credentials to `False` (or raise) when origins is wildcard, regardless of what the user configured.
- `explorer_username` / `explorer_password` left blank: no admin user is seeded and login always fails — this is the safe default for shared images.

## Acceptance Criteria
- [ ] `Settings` loads all fields from env vars and `.env` file.
- [ ] Missing required fields raise `pydantic.ValidationError` with descriptive messages.
- [ ] `get_settings()` returns a cached singleton instance.
- [ ] `jwt_secret_key` has no insecure default value.
- [ ] `api_mode` validates against `rest`, `graphql`, `both` only.
- [ ] `auth_mode` validates against `jwt`, `basic`, `both` only.
- [ ] `cors_origins` defaults to `["*"]`; `cors_allow_credentials` defaults to `False`.
- [ ] When `cors_origins == ["*"]`, `cors_allow_credentials` is forced to `False` at app construction.
- [ ] `explorer_username` and `explorer_password` are both optional and default to `None`.

## Module Location
`src/dbzap/core/config.py`

## Dependencies
- `pydantic-settings`
