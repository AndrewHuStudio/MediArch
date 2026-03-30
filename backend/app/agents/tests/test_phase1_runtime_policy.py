import importlib
import importlib.util


MODULE_NAME = "backend.app.agents.runtime_policy"


def _load_runtime_policy():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "runtime_policy module should exist"
    return importlib.import_module(MODULE_NAME)


def test_phase1_runtime_mode_is_forced_to_parallel():
    runtime_policy = _load_runtime_policy()

    info = runtime_policy.resolve_phase1_runtime_mode("neo4j_first")

    assert info["configured_mode"] == "neo4j_first"
    assert info["effective_mode"] == "parallel"
    assert info["is_forced"] is True


def test_phase1_runtime_diagnostics_expose_configured_and_effective_mode():
    runtime_policy = _load_runtime_policy()

    diagnostics = runtime_policy.build_phase1_runtime_diagnostics("neo4j_first")

    assert diagnostics["phase1_retrieval_mode"]["configured"] == "neo4j_first"
    assert diagnostics["phase1_retrieval_mode"]["effective"] == "parallel"
    assert diagnostics["phase1_retrieval_mode"]["is_forced"] is True


def test_checkpointer_runtime_falls_back_to_sqlite_when_postgres_backend_missing():
    runtime_policy = _load_runtime_policy()

    info = runtime_policy.resolve_checkpointer_runtime_status(
        "postgres",
        is_langgraph_api=False,
        sqlite_available=True,
        postgres_available=False,
    )

    assert info["configured_backend"] == "postgres"
    assert info["effective_backend"] == "sqlite"
    assert info["fallback_reason"] == "missing_postgres_backend"
    assert info["is_fallback"] is True


def test_store_runtime_falls_back_to_sqlite_when_postgres_backend_missing():
    runtime_policy = _load_runtime_policy()

    info = runtime_policy.resolve_store_runtime_status(
        "postgres",
        is_langgraph_api=False,
        sqlite_available=True,
        postgres_available=False,
    )

    assert info["configured_backend"] == "postgres"
    assert info["effective_backend"] == "sqlite"
    assert info["fallback_reason"] == "missing_postgres_backend"
    assert info["is_fallback"] is True
