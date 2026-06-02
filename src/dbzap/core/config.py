from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str

    # Auth
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # API mode
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

    @field_validator("jwt_secret_key")
    @classmethod
    def jwt_secret_key_must_be_set(cls, v: str) -> str:
        if not v:
            raise ValueError("jwt_secret_key must not be empty")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
