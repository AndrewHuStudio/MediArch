from pathlib import Path
import importlib
import importlib.util
import sys
import types

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MODULE_NAME = "backend.app.agents.postgres_deployment_policy"


def _load_policy_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "postgres_deployment_policy module should exist"
    return importlib.import_module(MODULE_NAME)


def test_validate_required_postgres_persistence_rejects_fallback():
    policy = _load_policy_module()

    with pytest.raises(RuntimeError, match="checkpointer"):
        policy.validate_required_postgres_persistence(
            require_postgres=True,
            component_statuses={
                "checkpointer": {
                    "configured_backend": "postgres",
                    "effective_backend": "sqlite",
                    "fallback_reason": "missing_postgres_backend",
                },
                "store": {
                    "configured_backend": "postgres",
                    "effective_backend": "postgres",
                    "fallback_reason": None,
                },
                "api_sessions": {
                    "configured_backend": "postgres",
                    "effective_backend": "postgres",
                    "fallback_reason": None,
                },
            },
        )


def test_validate_required_postgres_persistence_accepts_effective_postgres():
    policy = _load_policy_module()

    policy.validate_required_postgres_persistence(
        require_postgres=True,
        component_statuses={
            "checkpointer": {
                "configured_backend": "postgres",
                "effective_backend": "postgres",
                "fallback_reason": None,
            },
            "store": {
                "configured_backend": "postgres",
                "effective_backend": "postgres",
                "fallback_reason": None,
            },
            "api_sessions": {
                "configured_backend": "postgres",
                "effective_backend": "postgres",
                "fallback_reason": None,
            },
        },
    )


def test_get_shared_postgres_uri_prefers_shared_env(monkeypatch):
    policy = _load_policy_module()
    monkeypatch.setenv("PERSISTENCE_POSTGRES_URI", "postgresql://shared-user:pw@db:5432/mediarch?sslmode=disable")
    monkeypatch.delenv("POSTGRES_CHECKPOINT_URI", raising=False)
    monkeypatch.delenv("POSTGRES_STORE_URI", raising=False)
    monkeypatch.delenv("POSTGRES_SESSION_STORE_URI", raising=False)

    assert (
        policy.get_shared_postgres_uri()
        == "postgresql://shared-user:pw@db:5432/mediarch?sslmode=disable"
    )


def test_session_store_default_uri_reuses_shared_postgres_uri(monkeypatch):
    monkeypatch.setenv(
        "PERSISTENCE_POSTGRES_URI",
        "postgresql://shared-user:pw@db:5432/mediarch?sslmode=disable",
    )
    monkeypatch.delenv("POSTGRES_SESSION_STORE_URI", raising=False)
    monkeypatch.delenv("POSTGRES_STORE_URI", raising=False)
    monkeypatch.delenv("POSTGRES_CHECKPOINT_URI", raising=False)

    sys.modules.pop("backend.api.session_store", None)
    session_store = importlib.import_module("backend.api.session_store")

    assert (
        session_store._default_session_store_uri()
        == "postgresql://shared-user:pw@db:5432/mediarch?sslmode=disable"
    )


def test_api_startup_validation_rejects_persistence_fallback(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    import backend.api.main as api_main

    monkeypatch.setattr(api_main.settings, "REQUIRE_POSTGRES_PERSISTENCE", True, raising=False)
    monkeypatch.setattr(
        api_main.chat,
        "SESSION_REPOSITORY_RUNTIME_STATUS",
        {
            "configured_backend": "postgres",
            "effective_backend": "postgres",
            "fallback_reason": None,
        },
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "backend.app.agents.mediarch_graph",
        types.SimpleNamespace(
            CHECKPOINTER_RUNTIME_STATUS={
                "configured_backend": "postgres",
                "effective_backend": "sqlite",
                "fallback_reason": "missing_postgres_backend",
            },
            STORE_RUNTIME_STATUS={
                "configured_backend": "postgres",
                "effective_backend": "postgres",
                "fallback_reason": None,
            },
        ),
    )

    with pytest.raises(RuntimeError, match="checkpointer"):
        api_main._validate_required_persistence_backends()
