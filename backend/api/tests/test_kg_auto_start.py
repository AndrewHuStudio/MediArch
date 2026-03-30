import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api
from data_process.schemas import TaskStatus


def test_maybe_start_auto_kg_build_on_startup_maps_r2_to_b3(monkeypatch):
    data_process_api._tasks.clear()
    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)
    monkeypatch.setenv("DATA_PROCESS_AUTO_START_KG", "1")
    monkeypatch.setenv("DATA_PROCESS_AUTO_START_KG_STRATEGY", "R2")
    monkeypatch.setenv("DATA_PROCESS_AUTO_START_KG_DOC_IDS", "doc-1, doc-2")

    started = []

    monkeypatch.setattr(
        data_process_api,
        "_start_kg_worker",
        lambda task_id, payload, loop=None: started.append((task_id, payload)),
    )

    task_id = data_process_api._maybe_start_auto_kg_build_on_startup()

    assert task_id is not None
    assert len(started) == 1
    _, payload = started[0]
    assert payload["strategy"] == "B3"
    assert payload["mongo_doc_ids"] == ["doc-1", "doc-2"]
    assert payload["source"] == "mongodb"
    assert data_process_api._tasks[task_id]["module"] == "kg"
    assert data_process_api._tasks[task_id]["status"] == TaskStatus.PENDING


def test_maybe_start_auto_kg_build_on_startup_skips_when_active_task_exists(monkeypatch):
    data_process_api._tasks.clear()
    data_process_api._tasks["kg-task-existing"] = {
        "status": TaskStatus.RUNNING,
        "module": "kg",
        "created_at": "2026-03-28T12:00:00",
        "request_payload": {
            "source": "mongodb",
            "mongo_doc_ids": ["doc-1"],
            "strategy": "B3",
        },
    }
    monkeypatch.setenv("DATA_PROCESS_AUTO_START_KG", "1")
    monkeypatch.setenv("DATA_PROCESS_AUTO_START_KG_STRATEGY", "B3")

    started = []
    monkeypatch.setattr(
        data_process_api,
        "_start_kg_worker",
        lambda task_id, payload, loop=None: started.append((task_id, payload)),
    )

    task_id = data_process_api._maybe_start_auto_kg_build_on_startup()

    assert task_id is None
    assert started == []
