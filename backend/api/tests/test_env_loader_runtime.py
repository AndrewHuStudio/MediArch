import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.env_loader import load_dotenv


def test_load_dotenv_preserves_existing_runtime_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "POSTGRES_CHECKPOINT_URI=postgresql://postgres:pw@localhost:5432/from_file",
                "MONGODB_URI=mongodb://admin:pw@localhost:27017/",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv(
        "POSTGRES_CHECKPOINT_URI",
        "postgresql://postgres:pw@postgres:5432/from_runtime",
    )
    monkeypatch.setenv("MONGODB_URI", "mongodb://admin:pw@mongodb:27017/")

    loaded = load_dotenv(env_path)

    assert loaded is True
    assert os.environ["POSTGRES_CHECKPOINT_URI"] == "postgresql://postgres:pw@postgres:5432/from_runtime"
    assert os.environ["MONGODB_URI"] == "mongodb://admin:pw@mongodb:27017/"
