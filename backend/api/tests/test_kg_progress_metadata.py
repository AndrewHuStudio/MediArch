import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from fastapi import WebSocketDisconnect


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api


class _FakeCompletedWebSocket:
    def __init__(self):
        self.json_messages = []
        self.accepted = False
        self._receive_count = 0

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if self._receive_count == 0:
            self._receive_count += 1
            return "ping"
        raise WebSocketDisconnect()

    async def send_text(self, message):
        return None

    async def send_json(self, payload):
        self.json_messages.append(payload)


def test_build_kg_progress_extra_blends_runtime_and_history(monkeypatch):
    with data_process_api._kg_history_lock:
        data_process_api._kg_build_history.clear()
        data_process_api._kg_build_history["hist-1"] = {
            "build_id": "hist-1",
            "strategy": "B1",
            "timestamp": "2026-03-24T10:00:00",
            "build_time_seconds": 1000.0,
            "chunk_count": 100,
            "result": {
                "total_entities": 0,
                "total_relations": 0,
                "total_triplets": 0,
                "quality_metrics": {},
            },
        }

    extra = data_process_api._build_kg_progress_extra(
        stage_name="relation_extraction",
        step_name="relation_extraction",
        current=24,
        total=100,
        elapsed_seconds=600.0,
        strategy="B1",
        total_chunks=100,
    )

    assert extra["overall_percent"] == 40
    assert extra["stage_percent"] == 25
    assert extra["current_display"] == 25
    assert extra["history_sample_count"] == 1
    assert extra["history_avg_seconds_per_chunk"] == 10.0
    assert extra["estimated_total_seconds"] == 1200
    assert extra["remaining_seconds"] == 600
    assert extra["estimate_source"] == "blended"


def test_build_kg_progress_extra_uses_history_when_runtime_fraction_missing():
    with data_process_api._kg_history_lock:
        data_process_api._kg_build_history.clear()
        data_process_api._kg_build_history["hist-2"] = {
            "build_id": "hist-2",
            "strategy": "B2",
            "timestamp": "2026-03-24T11:00:00",
            "build_time_seconds": 300.0,
            "chunk_count": 60,
            "result": {
                "total_entities": 0,
                "total_relations": 0,
                "total_triplets": 0,
                "quality_metrics": {},
            },
        }

    extra = data_process_api._build_kg_progress_extra(
        stage_name="unknown",
        step_name="unknown",
        current=0,
        total=0,
        elapsed_seconds=15.0,
        strategy="B2",
        total_chunks=120,
    )

    assert extra["overall_percent"] == 0
    assert extra["history_sample_count"] == 1
    assert extra["history_avg_seconds_per_chunk"] == 5.0
    assert extra["estimated_total_seconds"] == 600
    assert extra["remaining_seconds"] == 585
    assert extra["estimate_source"] == "history"


def test_build_kg_progress_extra_prefers_stage_calibrated_eta_when_available():
    with data_process_api._kg_history_lock:
        data_process_api._kg_build_history.clear()
        data_process_api._kg_build_history["hist-stage-1"] = {
            "build_id": "hist-stage-1",
            "strategy": "B1",
            "timestamp": "2026-03-24T12:00:00",
            "build_time_seconds": 1200.0,
            "chunk_count": 100,
            "stage_timings": {
                "ea_recognition": {"duration_seconds": 400.0, "unit_total": 100},
                "relation_extraction": {"duration_seconds": 500.0, "unit_total": 100},
                "triplet_optimization": {"duration_seconds": 120.0, "unit_total": 3},
                "cross_document_fusion": {"duration_seconds": 180.0, "unit_total": 3},
            },
            "result": {
                "total_entities": 0,
                "total_relations": 0,
                "total_triplets": 0,
                "quality_metrics": {},
            },
        }

    extra = data_process_api._build_kg_progress_extra(
        stage_name="relation_extraction",
        step_name="relation_extraction",
        current=24,
        total=100,
        elapsed_seconds=500.0,
        strategy="B1",
        total_chunks=100,
    )

    assert extra["overall_percent"] == 40
    assert extra["stage_percent"] == 25
    assert extra["estimated_total_seconds"] == 1177
    assert extra["remaining_seconds"] == 677
    assert extra["estimate_source"] == "stage_blended"
    assert extra["estimate_strategy"] == "stage_model"
    assert extra["history_sample_count"] == 1
    assert extra["stage_history_sample_count"] == 1
    assert extra["stage_history_avg_seconds_per_unit"] == 5.0
    assert extra["stage_history_sample_count_by_stage"] == {
        "ea_recognition": 1,
        "relation_extraction": 1,
        "triplet_optimization": 1,
        "cross_document_fusion": 1,
    }


def test_build_kg_stage_timings_payload_keeps_skipped_stages():
    payload = data_process_api._build_kg_stage_timings_payload(
        stage_durations_seconds={
            "ea_recognition": 321.25,
            "relation_extraction": 456.5,
            "cross_document_fusion": 78.0,
        },
        total_chunks=80,
        stage_results=[
            SimpleNamespace(stage="ea_recognition", stats={}),
            SimpleNamespace(stage="relation_extraction", stats={}),
            SimpleNamespace(stage="triplet_optimization", stats={"refinement_skipped": True}),
            SimpleNamespace(stage="cross_document_fusion", stats={"fusion_skipped": False}),
        ],
    )

    assert payload["ea_recognition"] == {
        "duration_seconds": 321.25,
        "unit_total": 80,
    }
    assert payload["relation_extraction"] == {
        "duration_seconds": 456.5,
        "unit_total": 80,
    }
    assert payload["triplet_optimization"] == {
        "duration_seconds": 0.0,
        "unit_total": 3,
    }
    assert payload["cross_document_fusion"] == {
        "duration_seconds": 78.0,
        "unit_total": 3,
    }


def test_build_kg_progress_extra_exposes_relation_judgement_percent():
    extra = data_process_api._build_kg_progress_extra(
        stage_name="cross_document_fusion",
        step_name="neo4j_write_progress",
        current=40,
        total=80,
        elapsed_seconds=120.0,
        strategy="B1",
        total_chunks=100,
    )

    assert extra["stage_percent"] == 50
    assert extra["relation_judgement_percent"] == 50
    assert extra["relation_judgement_processed"] == 40
    assert extra["relation_judgement_total"] == 80


def test_ws_progress_completed_message_keeps_backend_progress_extra():
    task_id = "kg-ws-completed"
    websocket = _FakeCompletedWebSocket()
    progress_extra = {
        "progress_kind": "kg_build",
        "overall_percent": 94,
        "estimate_source": "runtime",
        "step_label": "关系判断进度",
    }

    data_process_api._tasks.clear()
    data_process_api._tasks[task_id] = {
        "status": data_process_api.TaskStatus.COMPLETED,
        "module": "kg",
        "progress": {
            "stage": "cross_document_fusion:neo4j_write_progress",
            "current": 1500,
            "total": 1600,
            "message": "关系判断进度",
            "extra": progress_extra,
        },
        "result": {"ok": True},
        "error": None,
        "created_at": "2026-03-29T16:00:00",
    }

    asyncio.run(data_process_api.ws_progress(websocket, task_id))

    assert websocket.accepted is True
    assert websocket.json_messages
    done_message = websocket.json_messages[-1]
    assert done_message["stage"] == "done"
    assert done_message["extra"]["progress_kind"] == "kg_build"
    assert done_message["extra"]["overall_percent"] == 94
    assert done_message["extra"]["result"] == {"ok": True}
