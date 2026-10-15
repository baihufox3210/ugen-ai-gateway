from functools import lru_cache

from pydantic import Field, model_validator
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

    gateway_api_key: str = ""
    rate_limit_requests: int = Field(default=60, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)
    request_timeout: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_runtime_security(self) -> "Settings":
        if self.app_env.casefold() == "production" and not self.gateway_api_key:
            raise ValueError("GATEWAY_API_KEY is required in production")
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
