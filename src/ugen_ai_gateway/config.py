from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "UGen AI Gateway"
    app_env: str = "development"

    local_api_key: str = "local"
    local_base_url: str = ""
    local_model: str = ""

    cloud_api_key: str = ""
    cloud_base_url: str = ""
    cloud_model: str = ""

    request_timeout: float = 60.0
    max_retries: int = 2

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
