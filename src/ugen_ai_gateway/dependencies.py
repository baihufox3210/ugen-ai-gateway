from functools import lru_cache

from ugen_ai_gateway.config import Settings, get_settings
from ugen_ai_gateway.providers.cloud import CloudProvider
from ugen_ai_gateway.providers.local import LocalProvider
from ugen_ai_gateway.providers.openai_client import create_openai_client
from ugen_ai_gateway.services.gateway import GatewayService


@lru_cache
def get_local_provider() -> LocalProvider:
    settings = get_settings()

    client = create_openai_client(
        api_key=settings.local_api_key,
        base_url=settings.local_base_url or None,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )

    return LocalProvider(client)


@lru_cache
def get_cloud_provider() -> CloudProvider:
    settings = get_settings()

    if not settings.cloud_api_key:
        raise ValueError("CLOUD_API_KEY is not configured")

    client = create_openai_client(
        api_key=settings.cloud_api_key,
        base_url=settings.cloud_base_url or None,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )

    return CloudProvider(client)


@lru_cache
def get_gateway_service() -> GatewayService:
    return GatewayService(
        settings=get_settings(),
        local_provider=get_local_provider(),
        cloud_provider=get_cloud_provider(),
    )


def get_app_settings() -> Settings:
    return get_settings()
