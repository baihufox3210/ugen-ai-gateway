from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # Redact personal data even when the request stays on the local model.
    redact_local: bool = True
    # Put the original values back into the model's reply before returning it.
    restore_redacted: bool = True

    # llm: always ask the local model. rules: regular expressions only.
    # hybrid: rules when the request stays local anyway, the local model otherwise.
    analysis_mode: Literal["llm", "rules", "hybrid"] = "hybrid"
    analysis_cache_size: int = Field(default=256, ge=0)
    analysis_cache_ttl_seconds: float = Field(default=600, gt=0)

    gateway_api_key: str = ""
    rate_limit_requests: int = Field(default=60, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)
    request_timeout: float = Field(default=300.0, gt=0, le=600)
    max_retries: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_runtime_security(self) -> "Settings":
        if self.app_env.casefold() == "production" and not self.gateway_api_key:
            raise ValueError("GATEWAY_API_KEY is required in production")
        return self

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
