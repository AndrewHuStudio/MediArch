import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.env_loader import load_project_env


def test_load_project_env_overrides_polluted_openai_vars(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "MEDIARCH_API_KEY=sk-correct-key",
                "MEDIARCH_LLM_BASE_URL=https://api.example.com/v1",
                "MEDIARCH_LLM_MODEL=deepseek-chat",
                "MEDIARCH_KG_BASE_URL=https://kg.example.com/v1",
                "MEDIARCH_KG_MODEL=deepseek-v3",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MEDIARCH_API_KEY", "sk-bad-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad-openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://bad.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "bad-model")
    monkeypatch.setenv("KG_OPENAI_API_KEY", "sk-bad-kg-key")

    loaded = load_project_env(env_path)

    assert loaded is True
    assert os.environ["MEDIARCH_API_KEY"] == "sk-correct-key"
    assert os.environ["MEDIARCH_LLM_BASE_URL"] == "https://api.example.com/v1"
    assert os.environ["MEDIARCH_LLM_MODEL"] == "deepseek-chat"
    assert os.environ["MEDIARCH_KG_BASE_URL"] == "https://kg.example.com/v1"
    assert os.environ["MEDIARCH_KG_MODEL"] == "deepseek-v3"
    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENAI_BASE_URL" not in os.environ
    assert "OPENAI_MODEL" not in os.environ
    assert "KG_OPENAI_API_KEY" not in os.environ


def test_load_project_env_uses_repo_root_env_by_default():
    default_env_path = Path(__file__).resolve().parents[3] / ".env"
    assert default_env_path.exists()
