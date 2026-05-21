import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api


def test_pipeline_overview_marks_kg_completed_from_build_history_when_no_active_task(monkeypatch):
    data_process_api._tasks.clear()
    data_process_api._kg_build_history.clear()

    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)
    monkeypatch.setattr(data_process_api, "CATEGORIES", ["标准规范"])
    monkeypatch.setattr(
        data_process_api,
        "DOCUMENTS_DIR",
        PROJECT_ROOT / "data_process" / "documents",
    )
    monkeypatch.setattr(data_process_api, "_load_vectorized_document_index", lambda: {})
    monkeypatch.setattr(data_process_api, "_has_materialized_kg_graph", lambda: True)

    data_process_api._kg_build_history["build-1"] = {
        "build_id": "build-1",
        "strategy": "B3",
        "timestamp": "2026-04-25T12:00:00",
        "build_time_seconds": 12.5,
        "chunk_count": 3,
        "result": {
            "total_entities": 10,
            "total_relations": 4,
            "total_triplets": 20,
            "nodes_written": 15,
            "edges_written": 18,
            "quality_metrics": {},
        },
    }

    overview = asyncio.run(data_process_api.pipeline_overview())

    assert overview["summary"]["kg"]["status"] == "completed"
    assert overview["summary"]["kg"]["progress_percent"] == 100
