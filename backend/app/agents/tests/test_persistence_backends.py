import importlib
import importlib.util
from pathlib import Path
import sys

from langgraph.checkpoint.base import empty_checkpoint


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MODULE_NAME = "backend.app.agents.persistence"


def _load_persistence_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "persistence module should exist"
    return importlib.import_module(MODULE_NAME)


def test_sqlite_store_persists_items_across_instances(tmp_path):
    persistence = _load_persistence_module()
    db_path = tmp_path / "memory_store.sqlite"

    store = persistence.SQLiteStore(db_path)
    namespace = ("users", "user-1")
    store.put(namespace, "prefs", {"focus_areas": {"门诊": 2}})

    reloaded_store = persistence.SQLiteStore(db_path)
    item = reloaded_store.get(namespace, "prefs")

    assert item is not None
    assert item.value["focus_areas"]["门诊"] == 2

    history_ns = ("users", "user-1", "conversations", "session-1")
    store.put(history_ns, "1", {"role": "user", "content": "hello", "timestamp": "1"})

    results = reloaded_store.search(history_ns, limit=10)

    assert len(results) == 1
    assert results[0].value["content"] == "hello"


def test_sqlite_checkpointer_persists_checkpoints_across_instances(tmp_path):
    persistence = _load_persistence_module()
    db_path = tmp_path / "checkpoints.sqlite"

    saver = persistence.SQLiteCheckpointSaver(db_path)
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}

    checkpoint = empty_checkpoint()
    version = saver.get_next_version(None, None)
    checkpoint["channel_values"] = {"messages": [{"role": "user", "content": "hi"}]}
    checkpoint["channel_versions"] = {"messages": version}

    saved_config = saver.put(
        config,
        checkpoint,
        {"source": "test"},
        {"messages": version},
    )
    saver.put_writes(
        saved_config,
        [("messages", {"kind": "pending"})],
        task_id="task-1",
    )

    reloaded_saver = persistence.SQLiteCheckpointSaver(db_path)
    checkpoint_tuple = reloaded_saver.get_tuple(saved_config)

    assert checkpoint_tuple is not None
    assert checkpoint_tuple.checkpoint["id"] == checkpoint["id"]
    assert checkpoint_tuple.checkpoint["channel_values"]["messages"][0]["content"] == "hi"
    assert checkpoint_tuple.metadata["source"] == "test"
    assert checkpoint_tuple.pending_writes == [
        ("task-1", "messages", {"kind": "pending"})
    ]


def test_create_checkpointer_falls_back_to_sqlite_when_async_postgres_requires_event_loop(
    tmp_path,
    monkeypatch,
):
    persistence = _load_persistence_module()
    db_path = tmp_path / "fallback_checkpoints.sqlite"

    class FakeAsyncPostgresSaver:
        def __init__(self, conn):
            raise RuntimeError("no running event loop")

    monkeypatch.setattr(persistence, "POSTGRES_CHECKPOINTER_AVAILABLE", True)
    monkeypatch.setattr(persistence, "AsyncPostgresSaver", FakeAsyncPostgresSaver)

    saver = persistence.create_checkpointer_from_runtime(
        {"effective_backend": "postgres"},
        sqlite_path=db_path,
        postgres_uri="postgresql://postgres:test@localhost:5432/mediarch_checkpoints",
    )

    assert isinstance(saver, persistence.SQLiteCheckpointSaver)


def test_mediarch_graph_memory_helpers_persist_preferences_and_conversation_history(
    tmp_path,
    monkeypatch,
):
    store_path = tmp_path / "mediarch_store.sqlite"
    checkpoint_path = tmp_path / "mediarch_checkpoints.sqlite"

    monkeypatch.setenv("STORE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_STORE_PATH", str(store_path))
    monkeypatch.setenv("CHECKPOINT_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_CHECKPOINT_PATH", str(checkpoint_path))

    sys.modules.pop("backend.app.agents.mediarch_graph", None)
    mediarch_graph = importlib.import_module("backend.app.agents.mediarch_graph")

    mediarch_graph.save_user_preferences("user-1", {"focus_areas": {"ICU": 1}})
    mediarch_graph.save_conversation_turn("user-1", "session-1", "user", "hello")

    sys.modules.pop("backend.app.agents.mediarch_graph", None)
    reloaded_graph = importlib.import_module("backend.app.agents.mediarch_graph")

    preferences = reloaded_graph.get_user_preferences("user-1")
    history = reloaded_graph.get_conversation_history("user-1", "session-1", limit=10)

    assert preferences["focus_areas"]["ICU"] == 1
    assert any(item["content"] == "hello" for item in history)
