import pytest
from pydantic import ValidationError


BASE_ENV = {
    "database_url": "postgresql+asyncpg://user:pass@localhost/db",
    "jwt_secret_key": "supersecret",
}

# Uppercase env-var names used when setting os.environ (monkeypatch scenarios)
BASE_ENV_VARS = {
    "DATABASE_URL": BASE_ENV["database_url"],
    "JWT_SECRET_KEY": BASE_ENV["jwt_secret_key"],
}


def _get_fresh_settings(**overrides: str):
    """Construct Settings directly with lowercase field-name kwargs."""
    from dbzap.core.config import Settings

    # overrides may be uppercase (env-var style); normalise to lowercase
    normalised = {k.lower(): v for k, v in overrides.items()}
    env = {**BASE_ENV, **normalised}
    return Settings(**env)  # type: ignore[arg-type]


class TestSettingsLoading:
    def test_loads_required_fields(self):
        s = _get_fresh_settings()
        assert s.database_url == BASE_ENV["database_url"]
        assert s.jwt_secret_key == BASE_ENV["jwt_secret_key"]

    def test_defaults(self):
        s = _get_fresh_settings()
        assert s.jwt_algorithm == "HS256"
        assert s.jwt_expire_minutes == 60
        assert s.host == "0.0.0.0"
        assert s.port == 8000
        assert s.api_mode == "both"
        assert s.enable_explorer is True
        assert s.db_pool_size == 10
        assert s.db_max_overflow == 20
        assert s.db_pool_timeout == 30
        assert s.db_pool_recycle == 1800
        assert s.db_statement_timeout == "5s"

    def test_missing_database_url_raises(self):
        from dbzap.core.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(jwt_secret_key="secret")  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        fields = {e["loc"][0] for e in errors}
        assert "database_url" in fields

    def test_missing_jwt_secret_key_raises(self):
        from dbzap.core.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(database_url="postgresql+asyncpg://u:p@h/d")  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        fields = {e["loc"][0] for e in errors}
        assert "jwt_secret_key" in fields

    def test_invalid_api_mode_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _get_fresh_settings(API_MODE="invalid")
        errors = exc_info.value.errors()
        fields = {e["loc"][0] for e in errors}
        assert "api_mode" in fields

    def test_valid_api_modes(self):
        for mode in ("rest", "graphql", "both"):
            s = _get_fresh_settings(API_MODE=mode)
            assert s.api_mode == mode

    def test_jwt_secret_key_has_no_default(self):
        """jwt_secret_key must be required — no insecure default."""
        import inspect
        from dbzap.core.config import Settings

        fields = Settings.model_fields
        field = fields["jwt_secret_key"]
        assert field.default is None or str(field.default) == "PydanticUndefined"


class TestGetSettings:
    def test_returns_settings_instance(self, monkeypatch):
        for k, v in BASE_ENV_VARS.items():
            monkeypatch.setenv(k, v)

        from dbzap.core import config as config_module

        config_module.get_settings.cache_clear()
        s = config_module.get_settings()
        from dbzap.core.config import Settings

        assert isinstance(s, Settings)

    def test_returns_cached_singleton(self, monkeypatch):
        for k, v in BASE_ENV_VARS.items():
            monkeypatch.setenv(k, v)

        from dbzap.core import config as config_module

        config_module.get_settings.cache_clear()
        s1 = config_module.get_settings()
        s2 = config_module.get_settings()
        assert s1 is s2

    def test_env_vars_take_precedence_over_defaults(self, monkeypatch):
        for k, v in BASE_ENV_VARS.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("PORT", "9999")

        from dbzap.core import config as config_module

        config_module.get_settings.cache_clear()
        s = config_module.get_settings()
        assert s.port == 9999
        config_module.get_settings.cache_clear()
