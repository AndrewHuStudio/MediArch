import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api
from data_process.schemas import TaskStatus


def test_sync_progress_factory_closes_coroutine_when_schedule_fails(monkeypatch):
    task_id = "kg-task-1"
    data_process_api._tasks.clear()
    data_process_api._tasks[task_id] = {
        "status": TaskStatus.RUNNING,
        "module": "kg",
        "progress": None,
        "result": None,
        "error": None,
        "created_at": "2026-03-24T00:00:00",
    }
    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)

    captured = {}

    def fake_run_coroutine_threadsafe(coro, loop):
        captured["coro"] = coro
        raise RuntimeError("loop closed")

    monkeypatch.setattr(
        data_process_api.asyncio,
        "run_coroutine_threadsafe",
        fake_run_coroutine_threadsafe,
    )

    callback = data_process_api._sync_progress_factory(task_id, "kg", loop=object())
    callback("ea_recognition", 1, 8)

    assert data_process_api._tasks[task_id]["progress"]["stage"] == "ea_recognition"
    assert "coro" in captured
    assert captured["coro"].cr_frame is None
