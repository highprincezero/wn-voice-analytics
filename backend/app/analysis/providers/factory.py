from app.analysis.providers.azure import AzureIntelligence
from app.analysis.providers.mock import MockIntelligence
from app.config import get_settings


def get_intelligence() -> MockIntelligence | AzureIntelligence:
    provider = get_settings().llm_provider
    if provider == "azure":
        return AzureIntelligence()
    if provider == "mock":
        return MockIntelligence()
    raise RuntimeError("LLM_PROVIDER must be mock or azure")
