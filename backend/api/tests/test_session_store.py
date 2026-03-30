from pathlib import Path
import importlib
import importlib.util
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MODULE_NAME = "backend.api.session_store"


def _load_session_store_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "session_store module should exist"
    return importlib.import_module(MODULE_NAME)


def test_session_store_persists_session_metadata_and_history_across_instances(tmp_path):
    session_store = _load_session_store_module()
    db_path = tmp_path / "api_sessions.sqlite"

    repo = session_store.build_session_store_repository(
        max_history=20,
        backend="sqlite",
        sqlite_path=db_path,
    )
    session_id = repo.get_or_create_session("session-1")
    repo.add_message(session_id, "user", "hello")
    repo.add_message(
        session_id,
        "assistant",
        "hi",
        citations=[{"source": "doc-a", "location": "p1", "snippet": "x"}],
        images=["/img/a.png"],
    )
    repo.update_session(session_id, title="Custom Title", is_pinned=True)

    reloaded_repo = session_store.build_session_store_repository(
        max_history=20,
        backend="sqlite",
        sqlite_path=db_path,
    )
    sessions = reloaded_repo.list_sessions()
    history = reloaded_repo.get_session_history(session_id)

    assert len(sessions) == 1
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["title"] == "Custom Title"
    assert sessions[0]["is_pinned"] is True
    assert sessions[0]["message_count"] == 2
    assert [item["content"] for item in history] == ["hello", "hi"]
    assert history[1]["images"] == ["/img/a.png"]


def test_session_store_trims_history_and_preserves_sort_order(tmp_path):
    session_store = _load_session_store_module()
    db_path = tmp_path / "api_sessions.sqlite"

    repo = session_store.build_session_store_repository(
        max_history=3,
        backend="sqlite",
        sqlite_path=db_path,
    )
    older = repo.get_or_create_session("session-older")
    newer = repo.get_or_create_session("session-newer")

    for index in range(5):
        repo.add_message(older, "user", f"msg-{index}")
    repo.add_message(newer, "user", "new session first message")
    repo.update_session(older, is_pinned=True)

    sessions = repo.list_sessions()
    history = repo.get_session_history(older)

    assert sessions[0]["session_id"] == older
    assert sessions[1]["session_id"] == newer
    assert sessions[1]["title"] == "new session first message"
    assert [item["content"] for item in history] == ["msg-2", "msg-3", "msg-4"]


def test_session_store_preserves_insertion_order_when_timestamps_match(tmp_path, monkeypatch):
    session_store = _load_session_store_module()
    db_path = tmp_path / "api_sessions.sqlite"

    time_values = iter([
        100.0,  # create session
        101.0, 101.0, 101.0,  # add 3 messages with same timestamp
    ])
    monkeypatch.setattr(session_store.time, "time", lambda: next(time_values))
    uuid_values = iter(["cccc", "aaaa", "bbbb"])
    monkeypatch.setattr(
        session_store.uuid,
        "uuid4",
        lambda: type("FakeUUID", (), {"hex": next(uuid_values)})(),
    )

    repo = session_store.build_session_store_repository(
        max_history=10,
        backend="sqlite",
        sqlite_path=db_path,
    )
    session_id = repo.get_or_create_session("session-order")
    repo.add_message(session_id, "user", "msg-a")
    repo.add_message(session_id, "user", "msg-b")
    repo.add_message(session_id, "user", "msg-c")

    history = repo.get_session_history(session_id)

    assert [item["content"] for item in history] == ["msg-a", "msg-b", "msg-c"]
