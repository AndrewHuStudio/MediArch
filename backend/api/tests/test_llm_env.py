import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.llm_env import (
    get_api_key,
    get_kg_base_url,
    get_kg_embedding_model,
    get_kg_model,
    get_kg_timeout,
    get_llm_base_url,
    get_llm_model,
    get_model_provider,
)


def test_llm_env_reads_only_mediarch_variables(monkeypatch):
    monkeypatch.setenv("MEDIARCH_API_KEY", "sk-mediarch")
    monkeypatch.setenv("MEDIARCH_LLM_BASE_URL", "https://mediarch.example.com/v1")
    monkeypatch.setenv("MEDIARCH_LLM_MODEL", "mediarch-chat")
    monkeypatch.setenv("MEDIARCH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("MEDIARCH_KG_BASE_URL", "https://kg.example.com/v1")
    monkeypatch.setenv("MEDIARCH_KG_MODEL", "mediarch-kg")
    monkeypatch.setenv("MEDIARCH_KG_TIMEOUT", "45")
    monkeypatch.setenv("MEDIARCH_KG_EMBEDDING_MODEL", "kg-embed")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://legacy.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "legacy-chat")
    monkeypatch.setenv("OPENAI_MODEL_PROVIDER", "legacy-provider")
    monkeypatch.setenv("KG_OPENAI_BASE_URL", "https://legacy-kg.example.com/v1")
    monkeypatch.setenv("KG_OPENAI_MODEL", "legacy-kg")
    monkeypatch.setenv("KG_OPENAI_TIMEOUT", "999")
    monkeypatch.setenv("KG_OPENAI_EMBEDDING_MODEL", "legacy-embed")

    assert get_api_key() == "sk-mediarch"
    assert get_llm_base_url() == "https://mediarch.example.com/v1"
    assert get_llm_model("fallback-model") == "mediarch-chat"
    assert get_model_provider() == "openai"
    assert get_kg_base_url() == "https://kg.example.com/v1"
    assert get_kg_model("fallback-kg") == "mediarch-kg"
    assert get_kg_timeout(120.0) == 45.0
    assert get_kg_embedding_model("fallback-embed") == "kg-embed"


def test_llm_env_defaults_when_mediarch_variables_missing(monkeypatch):
    for key in list(os.environ):
        if key.startswith("MEDIARCH_") or key.startswith("OPENAI_") or key.startswith("KG_OPENAI_"):
            monkeypatch.delenv(key, raising=False)

    assert get_api_key() is None
    assert get_llm_base_url() is None
    assert get_llm_model("fallback-model") == "fallback-model"
    assert get_model_provider() == "openai"
    assert get_kg_base_url() is None
    assert get_kg_model("fallback-kg") == "fallback-kg"
    assert get_kg_timeout(120.0) == 120.0
    assert get_kg_embedding_model("fallback-embed") == "fallback-embed"
