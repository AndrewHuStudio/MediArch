import os
from pathlib import Path
from typing import Optional, Union

from dotenv import dotenv_values, load_dotenv as _dotenv_load


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
LEGACY_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENAI_MODEL_PROVIDER",
    "OPENAI_API_BASE",
    "KG_OPENAI_API_KEY",
    "KG_OPENAI_BASE_URL",
    "KG_OPENAI_MODEL",
    "KG_OPENAI_TIMEOUT",
    "KG_OPENAI_EMBEDDING_MODEL",
)


def _normalize_path(dotenv_path: Optional[Union[str, os.PathLike[str], Path]]) -> Path:
    if dotenv_path is None:
        return DEFAULT_ENV_PATH
    return Path(dotenv_path)


def _clear_declared_env_vars(dotenv_path: Path) -> None:
    for key in LEGACY_ENV_VARS:
        os.environ.pop(key, None)

    if not dotenv_path.exists():
        return

    for key in dotenv_values(dotenv_path).keys():
        if key:
            os.environ.pop(key, None)


def _clear_legacy_env_vars() -> None:
    for key in LEGACY_ENV_VARS:
        os.environ.pop(key, None)


def load_project_env(
    dotenv_path: Optional[Union[str, os.PathLike[str], Path]] = None,
) -> bool:
    env_path = _normalize_path(dotenv_path)
    _clear_declared_env_vars(env_path)
    return _dotenv_load(dotenv_path=env_path, override=True)


def load_dotenv(
    dotenv_path: Optional[Union[str, os.PathLike[str], Path]] = None,
    *args,
    **kwargs,
) -> bool:
    env_path = _normalize_path(dotenv_path)
    # Preserve variables injected by the runtime (for example Docker Compose).
    # Only remove legacy aliases so modern env names remain authoritative.
    _clear_legacy_env_vars()
    kwargs.setdefault("override", False)
    return _dotenv_load(dotenv_path=env_path, *args, **kwargs)
