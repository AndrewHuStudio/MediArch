import os
from typing import Optional


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def get_api_key() -> Optional[str]:
    return _clean(os.getenv("MEDIARCH_API_KEY"))


def get_llm_base_url() -> Optional[str]:
    return _clean(os.getenv("MEDIARCH_LLM_BASE_URL"))


def get_llm_model(default: str) -> str:
    return _clean(os.getenv("MEDIARCH_LLM_MODEL")) or default


def get_model_provider(default: str = "openai") -> str:
    return _clean(os.getenv("MEDIARCH_LLM_PROVIDER")) or default


def get_kg_base_url() -> Optional[str]:
    return _clean(os.getenv("MEDIARCH_KG_BASE_URL")) or get_llm_base_url()


def get_kg_model(default: str) -> str:
    return _clean(os.getenv("MEDIARCH_KG_MODEL")) or get_llm_model(default)


def get_kg_timeout(default: float) -> float:
    raw = _clean(os.getenv("MEDIARCH_KG_TIMEOUT"))
    if raw is None:
        return default
    return float(raw)


def get_kg_embedding_model(default: str) -> str:
    return _clean(os.getenv("MEDIARCH_KG_EMBEDDING_MODEL")) or default
