import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api
from data_process.schemas import TaskStatus


def test_load_tasks_marks_resumable_kg_task_waiting_network(tmp_path, monkeypatch):
    store_file = tmp_path / "tasks.json"
    store_file.write_text(
        json.dumps(
            {
                "kg-task-1": {
                    "status": "running",
                    "module": "kg",
                    "created_at": "2026-03-26T10:00:00",
                    "resume_payload": {
                        "kind": "kg_build",
                        "strategy": "B3",
                        "build_signature": "sig-1",
                        "resume_from_stage": "triplet_optimization",
                        "ea_pairs": [{"entity_name": "门诊部", "entity_type": "功能分区"}],
                        "triplets": [{"subject": "门诊部", "relation": "包含", "object": "挂号区"}],
                    },
                    "request_payload": {
                        "source": "mongodb",
                        "mongo_doc_ids": ["doc-1"],
                        "strategy": "B3",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(data_process_api, "TASKS_STORE_FILE", store_file)
    data_process_api._tasks.clear()

    data_process_api._load_tasks()

    assert data_process_api._tasks["kg-task-1"]["status"] == TaskStatus.WAITING_NETWORK
    assert "resume automatically" in data_process_api._tasks["kg-task-1"]["error"]


def test_resume_kg_tasks_on_startup_restarts_waiting_task(monkeypatch):
    started = []
    data_process_api._tasks.clear()
    data_process_api._tasks["kg-task-2"] = {
        "status": TaskStatus.WAITING_NETWORK,
        "module": "kg",
        "created_at": "2026-03-26T10:05:00",
        "resume_payload": {
            "kind": "kg_build",
            "strategy": "B3",
            "build_signature": "sig-1",
            "resume_from_stage": "triplet_optimization",
            "ea_pairs": [{"entity_name": "门诊部", "entity_type": "功能分区"}],
            "triplets": [{"subject": "门诊部", "relation": "包含", "object": "挂号区"}],
        },
        "request_payload": {
            "source": "mongodb",
            "mongo_doc_ids": ["doc-1"],
            "strategy": "B3",
        },
    }

    monkeypatch.setattr(
        data_process_api,
        "_start_kg_worker",
        lambda task_id, payload, loop=None: started.append((task_id, payload)),
    )

    data_process_api._resume_kg_tasks_on_startup()

    assert started == [
        (
            "kg-task-2",
            {
                "source": "mongodb",
                "mongo_doc_ids": ["doc-1"],
                "strategy": "B3",
            },
        )
    ]


def test_kg_task_worker_persists_stage3_checkpoint_on_failure(monkeypatch):
    task_id = "kg-task-3"
    data_process_api._tasks.clear()
    data_process_api._tasks[task_id] = {
        "status": TaskStatus.PENDING,
        "module": "kg",
        "progress": None,
        "result": None,
        "error": None,
        "created_at": "2026-03-26T10:10:00",
    }

    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)
    monkeypatch.setattr(
        data_process_api,
        "_load_chunks_from_builder_db",
        lambda module, doc_ids=None: [{"chunk_id": "chunk-1", "content_type": "text", "content": "demo"}],
    )

    class _FakeModule:
        def __init__(self):
            self.EA_MAX_ROUNDS = 1
            self.EA_NEW_THRESHOLD = 1
            self.REL_MAX_ROUNDS = 1
            self.REL_NEW_THRESHOLD = 1

        def _configure_builder_runtime(self):
            return None

        def _build_runtime_signature(self, chunks):
            return "sig-3"

        def build_resume_artifacts_from_runtime_cache(self, chunks):
            return None

        def _serialize_ea_pairs(self, ea_pairs):
            return ea_pairs

        def _serialize_triplets(self, triplets):
            return triplets

        def build_kg(
            self,
            chunks,
            enable_fusion=None,
            progress_callback=None,
            resume_artifacts=None,
            stage_result_callback=None,
            stage_checkpoint_callback=None,
        ):
            stage_checkpoint_callback(
                "triplet_optimization",
                {
                    "substage": "relation_normalization_done",
                    "triplets": [
                        {
                            "subject": "门诊部",
                            "relation": "CONTAINS",
                            "object": "挂号区",
                            "confidence": 0.91,
                            "source_chunk_id": "chunk-1",
                            "properties": {},
                        }
                    ],
                    "stats": {"names_standardized": 1, "input_triplets": 1},
                },
            )
            raise RuntimeError("stage3 interrupted")

    monkeypatch.setattr(data_process_api, "_create_kg_module", lambda strategy="B1", custom_config=None: _FakeModule())

    data_process_api._kg_task_worker(
        task_id,
        {"source": "mongodb", "mongo_doc_ids": ["doc-1"], "strategy": "B3"},
        loop=None,
    )

    assert data_process_api._tasks[task_id]["status"] == TaskStatus.FAILED
    assert data_process_api._tasks[task_id]["resume_payload"]["resume_from_stage"] == "triplet_optimization"
    assert (
        data_process_api._tasks[task_id]["resume_payload"]["stage3_checkpoint"]["substage"]
        == "relation_normalization_done"
    )


def test_kg_task_worker_persists_stage4_checkpoint_on_failure(monkeypatch):
    task_id = "kg-task-4"
    data_process_api._tasks.clear()
    data_process_api._tasks[task_id] = {
        "status": TaskStatus.PENDING,
        "module": "kg",
        "progress": None,
        "result": None,
        "error": None,
        "created_at": "2026-03-26T10:20:00",
    }

    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)
    monkeypatch.setattr(
        data_process_api,
        "_load_chunks_from_builder_db",
        lambda module, doc_ids=None: [{"chunk_id": "chunk-1", "content_type": "text", "content": "demo"}],
    )

    class _FakeModule:
        def __init__(self):
            self.EA_MAX_ROUNDS = 1
            self.EA_NEW_THRESHOLD = 1
            self.REL_MAX_ROUNDS = 1
            self.REL_NEW_THRESHOLD = 1

        def _configure_builder_runtime(self):
            return None

        def _build_runtime_signature(self, chunks):
            return "sig-4"

        def build_resume_artifacts_from_runtime_cache(self, chunks):
            return None

        def _serialize_ea_pairs(self, ea_pairs):
            return ea_pairs

        def _serialize_triplets(self, triplets):
            return triplets

        def build_kg(
            self,
            chunks,
            enable_fusion=None,
            progress_callback=None,
            resume_artifacts=None,
            stage_result_callback=None,
            stage_checkpoint_callback=None,
        ):
            stage_checkpoint_callback(
                "cross_document_fusion",
                {
                    "substage": "neo4j_write",
                    "final_triplets": [
                        {
                            "subject": "门诊部",
                            "relation": "CONTAINS",
                            "object": "挂号区",
                            "confidence": 0.91,
                            "source_chunk_id": "chunk-1",
                            "properties": {},
                        }
                    ],
                    "write_progress": {
                        "processed_count": 1,
                        "verification_results": {"0": True},
                    },
                },
            )
            raise RuntimeError("stage4 interrupted")

    monkeypatch.setattr(data_process_api, "_create_kg_module", lambda strategy="B1", custom_config=None: _FakeModule())

    data_process_api._kg_task_worker(
        task_id,
        {"source": "mongodb", "mongo_doc_ids": ["doc-1"], "strategy": "B3"},
        loop=None,
    )

    assert data_process_api._tasks[task_id]["status"] == TaskStatus.FAILED
    assert data_process_api._tasks[task_id]["resume_payload"]["resume_from_stage"] == "cross_document_fusion"
    assert (
        data_process_api._tasks[task_id]["resume_payload"]["stage4_checkpoint"]["substage"]
        == "neo4j_write"
    )


def test_kg_task_worker_exposes_stage4_checkpoint_progress_extra(monkeypatch):
    task_id = "kg-task-5"
    data_process_api._tasks.clear()
    data_process_api._tasks[task_id] = {
        "status": TaskStatus.PENDING,
        "module": "kg",
        "progress": None,
        "result": None,
        "error": None,
        "created_at": "2026-03-26T10:30:00",
    }

    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)
    monkeypatch.setattr(
        data_process_api,
        "_load_chunks_from_builder_db",
        lambda module, doc_ids=None: [{"chunk_id": "chunk-1", "content_type": "text", "content": "demo"}],
    )

    class _FakeModule:
        def __init__(self):
            self.EA_MAX_ROUNDS = 1
            self.EA_NEW_THRESHOLD = 1
            self.REL_MAX_ROUNDS = 1
            self.REL_NEW_THRESHOLD = 1

        def _configure_builder_runtime(self):
            return None

        def _build_runtime_signature(self, chunks):
            return "sig-5"

        def build_resume_artifacts_from_runtime_cache(self, chunks):
            return None

        def _serialize_ea_pairs(self, ea_pairs):
            return ea_pairs

        def _serialize_triplets(self, triplets):
            return triplets

        def build_kg(
            self,
            chunks,
            enable_fusion=None,
            progress_callback=None,
            resume_artifacts=None,
            stage_result_callback=None,
            stage_checkpoint_callback=None,
        ):
            stage_checkpoint_callback(
                "cross_document_fusion",
                {
                    "substage": "latent_recognition",
                    "merge_map": {"旧门诊部": "门诊部"},
                    "fused_triplets": [
                        {
                            "subject": "门诊部",
                            "relation": "CONTAINS",
                            "object": "挂号区",
                            "confidence": 0.91,
                            "source_chunk_id": "chunk-1",
                            "properties": {},
                        }
                    ],
                    "latent_triplets": [
                        {
                            "subject": "门诊部",
                            "relation": "CONNECTS",
                            "object": "候诊区",
                            "confidence": 0.71,
                            "source_chunk_id": "latent_fusion",
                            "properties": {"inferred": True},
                        }
                    ],
                    "latent_rounds": 1,
                    "latent_new_counts": [1],
                    "latent_progress": {
                        "current_round": 1,
                        "next_batch_start": 10,
                        "current_round_new_count": 1,
                    },
                    "latent_candidate_pairs_total": 24,
                    "final_triplets": [
                        {
                            "subject": "门诊部",
                            "relation": "CONTAINS",
                            "object": "挂号区",
                            "confidence": 0.91,
                            "source_chunk_id": "chunk-1",
                            "properties": {},
                        },
                        {
                            "subject": "门诊部",
                            "relation": "CONNECTS",
                            "object": "候诊区",
                            "confidence": 0.71,
                            "source_chunk_id": "latent_fusion",
                            "properties": {"inferred": True},
                        },
                    ],
                },
            )
            raise RuntimeError("stage4 interrupted")

    monkeypatch.setattr(data_process_api, "_create_kg_module", lambda strategy="B1", custom_config=None: _FakeModule())

    data_process_api._kg_task_worker(
        task_id,
        {"source": "mongodb", "mongo_doc_ids": ["doc-1"], "strategy": "B3"},
        loop=None,
    )

    progress = data_process_api._tasks[task_id]["progress"]
    extra = progress["extra"]
    assert progress["stage"] == "cross_document_fusion:latent_recognition"
    assert extra["stage4_substage"] == "latent_recognition"
    assert extra["stage4_substage_label"] == "潜在关系识别"
    assert extra["stage4_entities_merged"] == 1
    assert extra["stage4_fused_triplets"] == 1
    assert extra["stage4_latent_triplets"] == 1
    assert extra["stage4_final_triplets"] == 2
    assert extra["stage4_latent_round"] == 1
    assert extra["stage4_latent_pairs_total"] == 24
