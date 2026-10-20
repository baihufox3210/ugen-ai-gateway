import logging
from functools import lru_cache

from ugen_ai_gateway.analysis.analyzer import RequestAnalyzer
from ugen_ai_gateway.analysis.ner import ENTITY_TYPES, build_presidio_finder
from ugen_ai_gateway.analysis.pii import register_finder
from ugen_ai_gateway.analysis.policy import RoutingPolicy
from ugen_ai_gateway.config import Settings, get_settings
from ugen_ai_gateway.providers.openai_client import create_openai_client
from ugen_ai_gateway.providers.openai_compatible import OpenAICompatibleProvider
from ugen_ai_gateway.routing.config import RoutingConfig, load_routing_config
from ugen_ai_gateway.services.errors import GatewayConfigurationError
from ugen_ai_gateway.services.gateway import GatewayService

logger = logging.getLogger(__name__)


@lru_cache
def get_local_provider() -> OpenAICompatibleProvider:
    settings = get_settings()

    client = create_openai_client(
        api_key=settings.local_api_key,
        base_url=settings.local_base_url or None,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )

    return OpenAICompatibleProvider(client, name="local", locality="local")


@lru_cache
def get_cloud_provider() -> OpenAICompatibleProvider:
    settings = get_settings()

    if not settings.cloud_api_key:
        raise ValueError("CLOUD_API_KEY is not configured")

    client = create_openai_client(
        api_key=settings.cloud_api_key,
        base_url=settings.cloud_base_url or None,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )

    return OpenAICompatibleProvider(client, name="cloud", locality="cloud")


@lru_cache
def get_gateway_service() -> GatewayService:
    settings = get_settings()
    if settings.pii_ner == "presidio":
        register_finder(
            build_presidio_finder(
                language=settings.pii_ner_language,
                spacy_model=settings.pii_ner_spacy_model,
                score_threshold=settings.pii_ner_score_threshold,
            ),
            types=[data_type for data_type, _ in ENTITY_TYPES.values()],
        )

    if settings.routing_config:
        return _service_from_routing_config(
            settings, load_routing_config(settings.routing_config)
        )

    local_provider = get_local_provider()
    return GatewayService(
        settings=settings,
        local_provider=local_provider,
        cloud_provider=(get_cloud_provider() if settings.cloud_api_key else None),
        analyzer=RequestAnalyzer(provider=local_provider, model=settings.local_model),
        policy=RoutingPolicy(
            cloud_enabled=bool(settings.cloud_api_key and settings.cloud_model)
        ),
    )


def _service_from_routing_config(
    settings: Settings, routing: RoutingConfig
) -> GatewayService:
    providers: dict[str, OpenAICompatibleProvider] = {}
    for config in routing.providers:
        api_key = config.resolved_api_key()
        if not api_key:
            logger.warning(
                "Provider %s has no API key (%s); routes to it are disabled",
                config.name,
                config.api_key_env or "api_key",
            )
            continue
        client = create_openai_client(
            api_key=api_key,
            base_url=config.base_url,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
        )
        providers[config.name] = OpenAICompatibleProvider(
            client, name=config.name, locality=config.locality
        )

    analysis_provider, analysis_model = routing.resolve(routing.analysis)
    local_provider, _ = routing.resolve(routing.defaults.local)
    for required in (analysis_provider.name, local_provider.name):
        if required not in providers:
            raise GatewayConfigurationError(f"Provider {required} is not usable")
    return GatewayService(
        settings=settings,
        local_provider=providers[local_provider.name],
        cloud_provider=None,
        analyzer=RequestAnalyzer(
            provider=providers[analysis_provider.name], model=analysis_model.id
        ),
        policy=RoutingPolicy(),
        routing=routing,
        providers=dict(providers),
    )


def get_app_settings() -> Settings:
    return get_settings()
