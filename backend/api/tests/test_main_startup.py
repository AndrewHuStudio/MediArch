from pathlib import Path
import importlib
import runpy
import sys
import types

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_module_launcher_sets_windows_selector_policy_before_uvicorn(monkeypatch):
    original_main = sys.modules.get("__main__")
    original_platform = sys.platform
    policy_calls: list[str] = []
    run_calls: list[dict[str, object]] = []

    class FakeAsyncio:
        @staticmethod
        def WindowsSelectorEventLoopPolicy():
            return "selector-policy"

        @staticmethod
        def set_event_loop_policy(policy):
            policy_calls.append(policy)

    fake_uvicorn = types.SimpleNamespace(
        run=lambda app, **kwargs: run_calls.append(
            {"app": app, "kwargs": kwargs, "policy_calls_seen": list(policy_calls)}
        )
    )

    monkeypatch.setitem(sys.modules, "asyncio", FakeAsyncio())
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["python"])
    sys.modules.pop("backend.api.__main__", None)

    try:
        runpy.run_module("backend.api", run_name="__main__")
    finally:
        monkeypatch.setattr(sys, "platform", original_platform)
        if original_main is not None:
            sys.modules["__main__"] = original_main
        else:
            sys.modules.pop("__main__", None)

    assert policy_calls == ["selector-policy"]
    assert run_calls == [
        {
            "app": "backend.api.main:app",
            "kwargs": {"host": "0.0.0.0", "port": 8010, "reload": True, "log_level": "info"},
            "policy_calls_seen": ["selector-policy"],
        }
    ]


@pytest.mark.asyncio
async def test_lifespan_falls_back_to_sqlite_immediately_when_optional_postgres_is_unreachable(
    monkeypatch,
):
    import backend.api.main as api_main
    import backend.app.agents.mediarch_graph as mediarch_graph_module
    import backend.app.agents.persistence as persistence_module

    fallback_paths: list[str] = []

    class FakePool:
        async def open(self):
            raise AssertionError("pool.open should not be called when endpoint probe fails")

    fake_graph = types.SimpleNamespace(checkpointer=types.SimpleNamespace(_pool=FakePool()))

    class FakeSQLiteCheckpointSaver:
        def __init__(self, path):
            fallback_paths.append(path)

    monkeypatch.setattr(api_main.settings, "PRELOAD_SUPERVISOR", True, raising=False)
    monkeypatch.setattr(api_main.settings, "REQUIRE_POSTGRES_PERSISTENCE", False, raising=False)
    monkeypatch.setattr(api_main, "_validate_required_persistence_backends", lambda: None)
    monkeypatch.setattr(api_main, "_postgres_endpoint_is_reachable", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(mediarch_graph_module, "graph", fake_graph, raising=False)
    monkeypatch.setattr(
        mediarch_graph_module,
        "SQLITE_CHECKPOINT_PATH",
        ".langgraph_api/checkpoints.db",
        raising=False,
    )
    monkeypatch.setattr(
        persistence_module,
        "SQLiteCheckpointSaver",
        FakeSQLiteCheckpointSaver,
        raising=False,
    )

    async with api_main.lifespan(api_main.app):
        pass

    assert fallback_paths == [".langgraph_api/checkpoints.db"]
    assert isinstance(fake_graph.checkpointer, FakeSQLiteCheckpointSaver)


@pytest.mark.asyncio
async def test_lifespan_fails_fast_when_required_postgres_is_unreachable(monkeypatch):
    import backend.api.main as api_main
    import backend.app.agents.mediarch_graph as mediarch_graph_module

    class FakePool:
        async def open(self):
            raise AssertionError("pool.open should not be called when endpoint probe fails")

    fake_graph = types.SimpleNamespace(checkpointer=types.SimpleNamespace(_pool=FakePool()))

    monkeypatch.setattr(api_main.settings, "PRELOAD_SUPERVISOR", True, raising=False)
    monkeypatch.setattr(api_main.settings, "REQUIRE_POSTGRES_PERSISTENCE", True, raising=False)
    monkeypatch.setattr(api_main, "_validate_required_persistence_backends", lambda: None)
    monkeypatch.setattr(api_main, "_postgres_endpoint_is_reachable", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(mediarch_graph_module, "graph", fake_graph, raising=False)
    monkeypatch.setattr(
        mediarch_graph_module,
        "SQLITE_CHECKPOINT_PATH",
        ".langgraph_api/checkpoints.db",
        raising=False,
    )
    monkeypatch.setattr(
        mediarch_graph_module,
        "POSTGRES_CHECKPOINT_URI",
        "postgresql://postgres:test@localhost:5432/mediarch_checkpoints",
        raising=False,
    )

    with pytest.raises(RuntimeError, match="PostgreSQL checkpoint endpoint is unreachable"):
        async with api_main.lifespan(api_main.app):
            pass
