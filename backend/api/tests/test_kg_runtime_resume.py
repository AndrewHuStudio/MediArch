import sys
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.databases.graph.builders.kg_builder import MedicalKGBuilder
from data_process.kg.kg_module import EAPair, KgModule

LONG_TEXT = "门诊部应靠近医院主入口，方便患者到达，并应与挂号区、候诊区形成清晰连续的就诊流线。"


class FakeRuntimeCacheCollection:
    def __init__(self):
        self.docs = {}

    def find_one(self, query):
        return self.docs.get(
            (query.get("build_signature"), query.get("stage"), query.get("chunk_id"))
        )

    def update_one(self, query, update, upsert=False):
        payload = dict(update.get("$set") or {})
        self.docs[(query.get("build_signature"), query.get("stage"), query.get("chunk_id"))] = payload
        return SimpleNamespace()


def _make_module(cache_collection, llm):
    module = KgModule.__new__(KgModule)
    module.config = {"name": "B3", "use_fusion": True, "use_latent_relations": True, "use_refinement": True}
    module.alias_map = {}
    module.llm = llm
    module.use_refinement = True
    module.EA_MAX_ROUNDS = 1
    module.EA_NEW_THRESHOLD = 99
    module.REL_MAX_ROUNDS = 1
    module.REL_NEW_THRESHOLD = 99
    module._active_build_signature = "build-sig"
    module._runtime_cache_collection = cache_collection
    module._get_schema_types = lambda: []
    module._get_relation_types = lambda: []
    module._build_ea_prompt = lambda *args, **kwargs: "ea-prompt"
    module._build_relation_prompt = lambda *args, **kwargs: "rel-prompt"
    return module


def test_stage1_reuses_cached_chunk_without_llm_call():
    cache = FakeRuntimeCacheCollection()
    cache.update_one(
        {"build_signature": "build-sig", "stage": "ea_recognition", "chunk_id": "chunk-1"},
        {
            "$set": {
                "build_signature": "build-sig",
                "stage": "ea_recognition",
                "chunk_id": "chunk-1",
                "rounds": 2,
                "payload": {
                    "ea_pairs": [
                        {
                            "entity_name": "门诊部",
                            "entity_type": "功能分区",
                            "description": "门诊区域",
                            "attributes": ["靠近主入口"],
                        }
                    ]
                },
            }
        },
        upsert=True,
    )

    llm = SimpleNamespace(chat_json=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")))
    module = _make_module(cache, llm)

    result = module.stage1_ea_recognition(
        [{"chunk_id": "chunk-1", "content_type": "text", "content": LONG_TEXT}]
    )

    assert len(result.ea_pairs) == 1
    assert result.ea_pairs[0].entity_name == "门诊部"
    assert result.stats["cached_chunks"] == 1


def test_stage2_persists_and_reuses_chunk_cache():
    cache = FakeRuntimeCacheCollection()
    llm_calls = {"count": 0}

    def chat_json(*args, **kwargs):
        llm_calls["count"] += 1
        return {"triples": [["门诊部", "靠近", "医院主入口", 0.91]]}

    module = _make_module(cache, SimpleNamespace(chat_json=chat_json))
    chunks = [{"chunk_id": "chunk-2", "content_type": "text", "content": LONG_TEXT}]
    ea_pairs = [
        EAPair(entity_name="门诊部", entity_type="功能分区", description="门诊区域", attributes=[]),
        EAPair(entity_name="医院主入口", entity_type="交通空间", description="主入口", attributes=[]),
    ]

    first = module.stage2_relation_extraction(chunks, ea_pairs)
    assert llm_calls["count"] == 1
    assert len(first.triplets) == 1
    assert first.stats["cached_chunks"] == 0

    second = module.stage2_relation_extraction(chunks, ea_pairs)
    assert llm_calls["count"] == 1
    assert len(second.triplets) == 1
    assert second.stats["cached_chunks"] == 1


def test_stage2_limits_entity_context_to_chunk_relevant_entities():
    cache = FakeRuntimeCacheCollection()
    captured = {}

    def chat_json(*args, **kwargs):
        return {"triples": [["门诊部", "靠近", "医院主入口", 0.91]]}

    module = _make_module(cache, SimpleNamespace(chat_json=chat_json))

    def capture_prompt(content, entity_list_json, relation_types_json, existing_triplets, round_num):
        captured["content"] = content
        captured["entity_list_json"] = entity_list_json
        return "rel-prompt"

    module._build_relation_prompt = capture_prompt

    chunks = [{"chunk_id": "chunk-3", "content_type": "text", "content": LONG_TEXT}]
    ea_pairs = [
        EAPair(entity_name="门诊部", entity_type="功能分区", description="门诊区域", attributes=["靠近主入口"]),
        EAPair(entity_name="医院主入口", entity_type="交通空间", description="主入口", attributes=[]),
        EAPair(entity_name="住院药房", entity_type="功能分区", description="住院药房", attributes=["发药"]),
        EAPair(entity_name="MRI室", entity_type="医技空间", description="磁共振检查室", attributes=["屏蔽"]),
    ]

    module.stage2_relation_extraction(chunks, ea_pairs)

    entity_context = captured["entity_list_json"]
    assert "门诊部" in entity_context
    assert "医院主入口" in entity_context
    assert "住院药房" not in entity_context
    assert "MRI室" not in entity_context


def test_build_kg_can_resume_from_stage3_artifacts():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"merges": {}}))
    chunks = [{"chunk_id": "chunk-9", "content_type": "text", "content": LONG_TEXT}]
    events = []

    def fail_stage1(*args, **kwargs):
        raise AssertionError("stage1 should be skipped when resume artifacts exist")

    def fail_stage2(*args, **kwargs):
        raise AssertionError("stage2 should be skipped when resume artifacts exist")

    def fake_stage3(triplets, ea_pairs, progress_callback=None, resume_artifacts=None, checkpoint_callback=None):
        events.append(("stage3", len(triplets), len(ea_pairs)))
        return SimpleNamespace(stage="triplet_optimization", rounds=1, triplets=triplets, stats={"resumed": False})

    def fake_stage4(triplets, ea_pairs, progress_callback=None, resume_artifacts=None, checkpoint_callback=None):
        events.append(("stage4", len(triplets), len(ea_pairs)))
        return SimpleNamespace(
            stage="cross_document_fusion",
            rounds=1,
            triplets=triplets,
            stats={"nodes_written": 2, "edges_written": 1},
        )

    module.stage1_ea_recognition = fail_stage1
    module.stage2_relation_extraction = fail_stage2
    module.stage3_triplet_optimization = fake_stage3
    module.stage4_cross_document_fusion = fake_stage4
    module._compute_quality_metrics = lambda triplets: {"triplet_count": len(triplets)}

    result = module.build_kg(
        chunks,
        resume_artifacts={
            "resume_from_stage": "triplet_optimization",
            "ea_pairs": [
                {
                    "entity_name": "门诊部",
                    "entity_type": "功能分区",
                    "description": "门诊区域",
                    "attributes": [],
                }
            ],
            "triplets": [
                {
                    "subject": "门诊部",
                    "relation": "靠近",
                    "object": "医院主入口",
                    "confidence": 0.91,
                    "source_chunk_id": "chunk-9",
                    "properties": {},
                }
            ],
        },
    )

    assert events == [("stage3", 1, 1), ("stage4", 1, 1)]
    assert result.total_triplets == 1
    assert result.nodes_written == 2


def test_build_resume_artifacts_from_runtime_cache_aggregates_stage1_and_stage2():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {}))
    cache.update_one(
        {"build_signature": "build-sig", "stage": "ea_recognition", "chunk_id": "chunk-1"},
        {
            "$set": {
                "build_signature": "build-sig",
                "stage": "ea_recognition",
                "chunk_id": "chunk-1",
                "rounds": 2,
                "payload": {
                    "ea_pairs": [
                        {
                            "entity_name": "门诊部",
                            "entity_type": "功能分区",
                            "description": "门诊区域",
                            "attributes": ["靠近主入口"],
                        }
                    ]
                },
            }
        },
        upsert=True,
    )
    cache.update_one(
        {"build_signature": "build-sig", "stage": "relation_extraction", "chunk_id": "chunk-1"},
        {
            "$set": {
                "build_signature": "build-sig",
                "stage": "relation_extraction",
                "chunk_id": "chunk-1",
                "rounds": 1,
                "payload": {
                    "triplets": [
                        {
                            "subject": "门诊部",
                            "relation": "靠近",
                            "object": "医院主入口",
                            "confidence": 0.91,
                            "source_chunk_id": "chunk-1",
                            "properties": {},
                        }
                    ]
                },
            }
        },
        upsert=True,
    )

    payload = module.build_resume_artifacts_from_runtime_cache(
        [{"chunk_id": "chunk-1", "content_type": "text", "content": LONG_TEXT}]
    )

    assert payload is not None
    assert payload["resume_from_stage"] == "triplet_optimization"
    assert payload["stage1_rounds"] == 2
    assert payload["stage2_rounds"] == 1
    assert len(payload["ea_pairs"]) == 1
    assert len(payload["triplets"]) == 1


def test_stage3_triplet_optimization_can_resume_from_relation_normalization_checkpoint(monkeypatch):
    cache = FakeRuntimeCacheCollection()
    module = _make_module(
        cache,
        SimpleNamespace(
            chat_json=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("LLM should not be called")
            )
        ),
    )

    monkeypatch.setattr(
        "data_process.kg.kg_module.normalize_relation",
        lambda _relation: (_ for _ in ()).throw(
            AssertionError("normalize_relation should not be called")
        ),
    )

    progress_events = []
    result = module.stage3_triplet_optimization(
        triplets=[
            SimpleNamespace(
                subject="原门诊部",
                relation="HAS_FEATURE",
                object="原挂号区",
                confidence=0.91,
                source_chunk_id="chunk-1",
                properties={},
            )
        ],
        ea_pairs=[
            EAPair(entity_name="门诊部", entity_type="功能分区", description="门诊区域", attributes=[]),
            EAPair(entity_name="挂号区", entity_type="功能分区", description="挂号区域", attributes=[]),
        ],
        progress_callback=lambda step, current, total: progress_events.append((step, current, total)),
        resume_artifacts={
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

    assert result.triplets[0].subject == "门诊部"
    assert result.triplets[0].relation == "CONTAINS"
    assert progress_events[-1] == ("validation_done", 3, 3)


def test_stage3_triplet_optimization_skips_llm_for_singleton_type_groups():
    cache = FakeRuntimeCacheCollection()
    llm_calls = []

    def _chat_json(*args, **kwargs):
        llm_calls.append((args, kwargs))
        return {"merges": {}}

    module = _make_module(cache, SimpleNamespace(chat_json=_chat_json))

    result = module.stage3_triplet_optimization(
        triplets=[
            SimpleNamespace(
                subject="医院甲",
                relation="REFERENCES",
                object="方法乙",
                confidence=0.91,
                source_chunk_id="chunk-1",
                properties={},
            )
        ],
        ea_pairs=[
            EAPair(entity_name="医院甲", entity_type="医院", description="", attributes=[]),
            EAPair(entity_name="方法乙", entity_type="设计方法", description="", attributes=[]),
        ],
    )

    assert len(llm_calls) == 0
    assert len(result.triplets) == 1


def test_build_kg_passes_stage3_checkpoint_into_triplet_optimization():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"merges": {}}))
    chunks = [{"chunk_id": "chunk-10", "content_type": "text", "content": LONG_TEXT}]
    captured = {}

    def fail_stage1(*args, **kwargs):
        raise AssertionError("stage1 should be skipped when resume artifacts exist")

    def fail_stage2(*args, **kwargs):
        raise AssertionError("stage2 should be skipped when resume artifacts exist")

    def fake_stage3(triplets, ea_pairs, progress_callback=None, resume_artifacts=None, checkpoint_callback=None):
        captured["resume_artifacts"] = resume_artifacts
        return SimpleNamespace(stage="triplet_optimization", rounds=1, triplets=triplets, stats={"resumed": True})

    def fake_stage4(triplets, ea_pairs, progress_callback=None, resume_artifacts=None, checkpoint_callback=None):
        return SimpleNamespace(
            stage="cross_document_fusion",
            rounds=1,
            triplets=triplets,
            stats={"nodes_written": 2, "edges_written": 1},
        )

    module.stage1_ea_recognition = fail_stage1
    module.stage2_relation_extraction = fail_stage2
    module.stage3_triplet_optimization = fake_stage3
    module.stage4_cross_document_fusion = fake_stage4
    module._compute_quality_metrics = lambda triplets: {"triplet_count": len(triplets)}

    module.build_kg(
        chunks,
        resume_artifacts={
            "resume_from_stage": "triplet_optimization",
            "ea_pairs": [
                {
                    "entity_name": "门诊部",
                    "entity_type": "功能分区",
                    "description": "门诊区域",
                    "attributes": [],
                }
            ],
            "triplets": [
                {
                    "subject": "门诊部",
                    "relation": "靠近",
                    "object": "医院主入口",
                    "confidence": 0.91,
                    "source_chunk_id": "chunk-10",
                    "properties": {},
                }
            ],
            "stage3_checkpoint": {
                "substage": "relation_normalization_done",
                "triplets": [
                    {
                        "subject": "门诊部",
                        "relation": "CONTAINS",
                        "object": "医院主入口",
                        "confidence": 0.91,
                        "source_chunk_id": "chunk-10",
                        "properties": {},
                    }
                ],
                "stats": {"names_standardized": 1, "input_triplets": 1},
            },
        },
    )

    assert captured["resume_artifacts"]["substage"] == "relation_normalization_done"


def test_stage4_cross_document_fusion_can_resume_from_neo4j_write_checkpoint():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))
    module.enable_fusion = True
    module.enable_latent_relations = False
    module.FUSION_MAX_ENTITY_PAIRS = 10
    module.FUSION_MAX_LATENT_PAIRS = 10
    module.FUSION_MAX_MULTI_SOURCE = 10
    module.FUSION_SIMILARITY = 0.99

    class _FakeGraph:
        def __init__(self):
            self.nodes = set()
            self.edges = []

        def number_of_nodes(self):
            return len(self.nodes)

        def number_of_edges(self):
            return len(self.edges)

        def add_edge(self, source, target, **attrs):
            self.edges.append((source, target, dict(attrs)))

    class _FakeBuilder:
        def __init__(self):
            self.lock = nullcontext()
            self.graph = _FakeGraph()
            self.verify_calls = 0
            self.override_history = []

        def _find_or_create_entity(self, entity_name, chunk_id, entity_type, description=None):
            self.graph.nodes.add((entity_name, entity_type))
            return entity_name

        def _add_single_triple_to_graph(
            self,
            triple,
            chunk_id,
            entity_types,
            entity_descriptions=None,
            content="",
            verification_override=None,
        ):
            subj, pred, obj = triple
            self.override_history.append(verification_override)
            if subj == "家化新生儿病房":
                if verification_override is None:
                    self.verify_calls += 1
                    return {
                        "added": False,
                        "verification_used": True,
                        "verification_result": False,
                    }
                return {
                    "added": False,
                    "verification_used": True,
                    "verification_result": bool(verification_override),
                }

            self.graph.add_edge(subj, obj, relation=pred, chunk_id=chunk_id)
            return {
                "added": True,
                "verification_used": verification_override is not None,
                "verification_result": verification_override,
            }

        def write_to_neo4j(self):
            return None

    module.kg_builder = _FakeBuilder()
    module._enrich_edge_metadata = lambda builder, triplets, entity_types: None

    ea_pairs = [
        EAPair(entity_name="家化新生儿病房", entity_type="空间", description="", attributes=[]),
        EAPair(entity_name="新生儿", entity_type="治疗方法", description="", attributes=[]),
        EAPair(entity_name="门诊部", entity_type="功能分区", description="", attributes=[]),
        EAPair(entity_name="医院主入口", entity_type="交通空间", description="", attributes=[]),
    ]
    triplets = [
        SimpleNamespace(
            subject="家化新生儿病房",
            relation="PROVIDES",
            object="新生儿",
            confidence=0.9,
            source_chunk_id="chunk-1",
            properties={},
        ),
        SimpleNamespace(
            subject="门诊部",
            relation="CONTAINS",
            object="医院主入口",
            confidence=0.91,
            source_chunk_id="chunk-2",
            properties={},
        ),
    ]

    result = module.stage4_cross_document_fusion(
        triplets,
        ea_pairs,
        resume_artifacts={
            "substage": "neo4j_write",
            "merge_map": {},
            "fused_triplets": module._serialize_triplets(triplets),
            "latent_triplets": [],
            "latent_rounds": 0,
            "latent_new_counts": [],
            "final_triplets": module._serialize_triplets(triplets),
            "write_progress": {
                "processed_count": 1,
                "verification_results": {"0": False},
            },
        },
    )

    assert result.stats["resumed_substage"] == "neo4j_write"
    assert module.kg_builder.verify_calls == 0
    assert module.kg_builder.override_history[0] is False
    assert result.stats["nodes_written"] == 4
    assert result.stats["edges_written"] == 1


def test_stage4_cross_document_fusion_emits_fusion_start_checkpoint_before_long_work():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))
    module.enable_fusion = True
    module.enable_latent_relations = False
    checkpoints = []

    ea_pairs = [
        EAPair(entity_name="门诊部", entity_type="功能分区", description="", attributes=[]),
        EAPair(entity_name="医院主入口", entity_type="交通空间", description="", attributes=[]),
    ]
    triplets = [
        SimpleNamespace(
            subject="门诊部",
            relation="CONTAINS",
            object="医院主入口",
            confidence=0.91,
            source_chunk_id="chunk-1",
            properties={},
        ),
    ]

    module.kg_builder = SimpleNamespace(
        lock=nullcontext(),
        graph=SimpleNamespace(number_of_nodes=lambda: 0, number_of_edges=lambda: 0),
        _find_or_create_entity=lambda *args, **kwargs: "node",
        _add_single_triple_to_graph=lambda *args, **kwargs: {"added": True, "verification_used": False, "verification_result": None},
        write_to_neo4j=lambda: None,
    )
    module._enrich_edge_metadata = lambda builder, triplets, entity_types: None

    module.stage4_cross_document_fusion(
        triplets,
        ea_pairs,
        checkpoint_callback=lambda payload: checkpoints.append(payload),
    )

    assert checkpoints
    assert checkpoints[0]["substage"] == "fusion_start"


def test_write_to_neo4j_batches_checkpoint_updates():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))
    module.NEO4J_WRITE_CHECKPOINT_EVERY = 2
    module.NEO4J_WRITE_BATCH_SIZE = 2

    class _FakeGraph:
        def __init__(self):
            self.nodes = set()
            self.edges = []

        def number_of_nodes(self):
            return len(self.nodes)

        def number_of_edges(self):
            return len(self.edges)

        def add_edge(self, source, target, **attrs):
            self.edges.append((source, target, dict(attrs)))

    class _FakeBuilder:
        def __init__(self):
            self.lock = nullcontext()
            self.graph = _FakeGraph()

        def _find_or_create_entity(self, entity_name, chunk_id, entity_type, description=None):
            self.graph.nodes.add((entity_name, entity_type))
            return entity_name

        def _add_single_triple_to_graph(
            self,
            triple,
            chunk_id,
            entity_types,
            entity_descriptions=None,
            content="",
            verification_override=None,
        ):
            subj, pred, obj = triple
            self.graph.add_edge(subj, obj, relation=pred, chunk_id=chunk_id)
            return {
                "added": True,
                "verification_used": True,
                "verification_result": True,
            }

        def write_to_neo4j(self):
            return None

    module.kg_builder = _FakeBuilder()
    module._enrich_edge_metadata = lambda builder, triplets, entity_types: None

    ea_pairs = [
        EAPair(entity_name="综合医院", entity_type="医院", description="", attributes=[]),
        EAPair(entity_name="门诊部", entity_type="部门", description="", attributes=[]),
        EAPair(entity_name="急诊部", entity_type="部门", description="", attributes=[]),
        EAPair(entity_name="手术部", entity_type="功能分区", description="", attributes=[]),
    ]
    triplets = [
        SimpleNamespace(subject="综合医院", relation="CONTAINS", object="门诊部", confidence=0.9, source_chunk_id="chunk-1", properties={}),
        SimpleNamespace(subject="综合医院", relation="CONTAINS", object="急诊部", confidence=0.9, source_chunk_id="chunk-2", properties={}),
        SimpleNamespace(subject="综合医院", relation="CONTAINS", object="手术部", confidence=0.9, source_chunk_id="chunk-3", properties={}),
    ]

    checkpoints = []
    module._write_to_neo4j(
        triplets,
        ea_pairs,
        merge_map={},
        checkpoint_state={"substage": "neo4j_write", "final_triplets": module._serialize_triplets(triplets)},
        checkpoint_callback=lambda payload: checkpoints.append(payload),
    )

    assert [item["write_progress"]["processed_count"] for item in checkpoints] == [2, 3]


def test_write_to_neo4j_uses_rule_split_batch_verify_and_progress_updates():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))
    module.NEO4J_WRITE_CHECKPOINT_EVERY = 2
    module.NEO4J_WRITE_BATCH_SIZE = 2
    module.NEO4J_VERIFY_BATCH_SIZE = 8

    class _FakeGraph:
        def __init__(self):
            self.nodes = set()
            self.edges = []

        def number_of_nodes(self):
            return len(self.nodes)

        def number_of_edges(self):
            return len(self.edges)

        def add_edge(self, source, target, **attrs):
            self.edges.append((source, target, dict(attrs)))

    class _FakeBuilder:
        def __init__(self):
            self.lock = nullcontext()
            self.graph = _FakeGraph()
            self.relation_llm_fallback = True
            self.type_synonyms = {}
            self.relation_constraints = {"CONTAINS": ({"医院"}, {"部门"})}
            self.allowed_relation_types = {"CONTAINS"}
            self.drop_uncertain_relations = False
            self.batch_verify_calls = []
            self.override_history = []

        def _find_or_create_entity(self, entity_name, chunk_id, entity_type, description=None):
            self.graph.nodes.add((entity_name, entity_type))
            return entity_name

        def _classify_relation_verification_need(
            self,
            subj,
            subj_type,
            relation,
            obj,
            obj_type,
            context="",
        ):
            if obj == "门诊部":
                return {"action": "accept", "reason": "rule_accept"}
            if obj == "透析机":
                return {"action": "reject", "reason": "rule_reject"}
            return {"action": "review", "reason": "needs_llm"}

        def _llm_verify_relations_batch(self, items):
            self.batch_verify_calls.append(list(items))
            return [
                {
                    "batch_index": 0,
                    "reasonable": False,
                    "reason": "review_reject",
                }
            ]

        def _add_single_triple_to_graph(
            self,
            triple,
            chunk_id,
            entity_types,
            entity_descriptions=None,
            content="",
            verification_override=None,
        ):
            subj, pred, obj = triple
            self.override_history.append((obj, verification_override))
            if verification_override is False:
                return {
                    "added": False,
                    "verification_used": True,
                    "verification_result": False,
                }
            self.graph.add_edge(subj, obj, relation=pred, chunk_id=chunk_id)
            return {
                "added": True,
                "verification_used": verification_override is not None,
                "verification_result": verification_override,
            }

        def write_to_neo4j(self):
            return None

    module.kg_builder = _FakeBuilder()
    module._enrich_edge_metadata = lambda builder, triplets, entity_types: None

    ea_pairs = [
        EAPair(entity_name="综合医院", entity_type="医院", description="", attributes=[]),
        EAPair(entity_name="门诊部", entity_type="部门", description="", attributes=[]),
        EAPair(entity_name="透析机", entity_type="医疗设备", description="", attributes=[]),
        EAPair(entity_name="ICU", entity_type="功能分区", description="", attributes=[]),
    ]
    triplets = [
        SimpleNamespace(subject="综合医院", relation="CONTAINS", object="门诊部", confidence=0.9, source_chunk_id="chunk-1", properties={}),
        SimpleNamespace(subject="综合医院", relation="CONTAINS", object="透析机", confidence=0.9, source_chunk_id="chunk-2", properties={}),
        SimpleNamespace(subject="综合医院", relation="CONTAINS", object="ICU", confidence=0.9, source_chunk_id="chunk-3", properties={}),
    ]

    checkpoints = []
    progress = []
    module._write_to_neo4j(
        triplets,
        ea_pairs,
        merge_map={},
        checkpoint_state={"substage": "neo4j_write", "final_triplets": module._serialize_triplets(triplets)},
        checkpoint_callback=lambda payload: checkpoints.append(payload),
        progress_callback=lambda step, current, total: progress.append((step, current, total)),
    )

    assert len(module.kg_builder.batch_verify_calls) == 1
    assert len(module.kg_builder.batch_verify_calls[0]) == 1
    assert [item["write_progress"]["processed_count"] for item in checkpoints] == [2, 3]
    assert checkpoints[-1]["write_progress"]["verification_results"] == {
        "0": True,
        "1": False,
        "2": False,
    }
    assert checkpoints[-1]["write_progress"]["review_items_done"] == 1
    assert checkpoints[-1]["write_progress"]["review_items_total"] == 1
    assert checkpoints[-1]["write_progress"]["review_batches_done"] == 1
    assert checkpoints[-1]["write_progress"]["review_batches_total"] == 1
    assert checkpoints[-1]["write_progress"]["accept_count"] == 1
    assert checkpoints[-1]["write_progress"]["reject_count"] == 2
    assert checkpoints[-1]["write_progress"]["skip_count"] == 0
    assert progress[-1] == ("neo4j_write_progress", 3, 3)


def test_write_to_neo4j_reuses_review_verdict_for_duplicate_semantic_relations():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))
    module.NEO4J_WRITE_CHECKPOINT_EVERY = 10
    module.NEO4J_WRITE_BATCH_SIZE = 10
    module.NEO4J_VERIFY_BATCH_SIZE = 10

    class _FakeGraph:
        def __init__(self):
            self.nodes = set()
            self.edges = []

        def number_of_nodes(self):
            return len(self.nodes)

        def number_of_edges(self):
            return len(self.edges)

        def add_edge(self, source, target, **attrs):
            self.edges.append((source, target, dict(attrs)))

    class _FakeBuilder:
        def __init__(self):
            self.lock = nullcontext()
            self.graph = _FakeGraph()
            self.relation_llm_fallback = True
            self.type_synonyms = {}
            self.relation_constraints = {"PROVIDES": ({"空间"}, {"治疗方法"})}
            self.allowed_relation_types = {"PROVIDES"}
            self.drop_uncertain_relations = False
            self.batch_verify_calls = []

        def _find_or_create_entity(self, entity_name, chunk_id, entity_type, description=None):
            self.graph.nodes.add((entity_name, entity_type))
            return entity_name

        def _classify_relation_verification_need(
            self,
            subj,
            subj_type,
            relation,
            obj,
            obj_type,
            context="",
        ):
            return {"action": "review", "reason": "needs_llm"}

        def _llm_verify_relations_batch(self, items):
            self.batch_verify_calls.append(list(items))
            return [
                {
                    "batch_index": 0,
                    "reasonable": True,
                    "reason": "duplicate_review_reused",
                }
            ]

        def _relation_verification_cache_key(
            self,
            subj,
            subj_type,
            relation,
            obj,
            obj_type,
            context="",
        ):
            return (
                str(subj).lower(),
                str(subj_type).lower(),
                str(relation).upper(),
                str(obj).lower(),
                str(obj_type).lower(),
                str(context).lower(),
            )

        def _add_single_triple_to_graph(
            self,
            triple,
            chunk_id,
            entity_types,
            entity_descriptions=None,
            content="",
            verification_override=None,
        ):
            subj, pred, obj = triple
            self.graph.add_edge(subj, obj, relation=pred, chunk_id=chunk_id, override=verification_override)
            return {
                "added": True,
                "verification_used": verification_override is not None,
                "verification_result": verification_override,
            }

        def write_to_neo4j(self):
            return None

    module.kg_builder = _FakeBuilder()
    module._enrich_edge_metadata = lambda builder, triplets, entity_types: None

    ea_pairs = [
        EAPair(entity_name="家化新生儿病房", entity_type="空间", description="", attributes=[]),
        EAPair(entity_name="新生儿", entity_type="治疗方法", description="", attributes=[]),
    ]
    triplets = [
        SimpleNamespace(subject="家化新生儿病房", relation="PROVIDES", object="新生儿", confidence=0.9, source_chunk_id="chunk-1", properties={}),
        SimpleNamespace(subject="家化新生儿病房", relation="PROVIDES", object="新生儿", confidence=0.88, source_chunk_id="chunk-2", properties={}),
    ]

    checkpoints = []
    module._write_to_neo4j(
        triplets,
        ea_pairs,
        merge_map={},
        checkpoint_state={"substage": "neo4j_write", "final_triplets": module._serialize_triplets(triplets)},
        checkpoint_callback=lambda payload: checkpoints.append(payload),
    )

    assert len(module.kg_builder.batch_verify_calls) == 1
    assert len(module.kg_builder.batch_verify_calls[0]) == 1
    assert checkpoints[-1]["write_progress"]["verification_results"] == {
        "0": True,
        "1": True,
    }
    assert checkpoints[-1]["write_progress"]["review_items_done"] == 1
    assert checkpoints[-1]["write_progress"]["review_items_total"] == 1


def test_write_to_neo4j_reuses_rule_classification_for_duplicate_semantic_relations():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))
    module.NEO4J_WRITE_CHECKPOINT_EVERY = 10
    module.NEO4J_WRITE_BATCH_SIZE = 10
    module.NEO4J_VERIFY_BATCH_SIZE = 10

    class _FakeGraph:
        def __init__(self):
            self.nodes = set()
            self.edges = []

        def number_of_nodes(self):
            return len(self.nodes)

        def number_of_edges(self):
            return len(self.edges)

        def add_edge(self, source, target, **attrs):
            self.edges.append((source, target, dict(attrs)))

    class _FakeBuilder:
        def __init__(self):
            self.lock = nullcontext()
            self.graph = _FakeGraph()
            self.relation_llm_fallback = True
            self.type_synonyms = {}
            self.relation_constraints = {"PROVIDES": ({"空间"}, {"治疗方法"})}
            self.allowed_relation_types = {"PROVIDES"}
            self.drop_uncertain_relations = False
            self.classify_calls = 0

        def _find_or_create_entity(self, entity_name, chunk_id, entity_type, description=None):
            self.graph.nodes.add((entity_name, entity_type))
            return entity_name

        def _classify_relation_verification_need(
            self,
            subj,
            subj_type,
            relation,
            obj,
            obj_type,
            context="",
        ):
            self.classify_calls += 1
            return {"action": "review", "reason": "needs_llm"}

        def _llm_verify_relations_batch(self, items):
            return [
                {
                    "batch_index": idx,
                    "reasonable": True,
                    "reason": "ok",
                }
                for idx, _ in enumerate(items)
            ]

        def _relation_verification_cache_key(
            self,
            subj,
            subj_type,
            relation,
            obj,
            obj_type,
            context="",
        ):
            return (
                str(subj).lower(),
                str(subj_type).lower(),
                str(relation).upper(),
                str(obj).lower(),
                str(obj_type).lower(),
                str(context).lower(),
            )

        def _add_single_triple_to_graph(
            self,
            triple,
            chunk_id,
            entity_types,
            entity_descriptions=None,
            content="",
            verification_override=None,
        ):
            subj, pred, obj = triple
            self.graph.add_edge(subj, obj, relation=pred, chunk_id=chunk_id, override=verification_override)
            return {
                "added": True,
                "verification_used": verification_override is not None,
                "verification_result": verification_override,
            }

        def write_to_neo4j(self):
            return None

    module.kg_builder = _FakeBuilder()
    module._enrich_edge_metadata = lambda builder, triplets, entity_types: None

    ea_pairs = [
        EAPair(entity_name="家化新生儿病房", entity_type="空间", description="", attributes=[]),
        EAPair(entity_name="新生儿", entity_type="治疗方法", description="", attributes=[]),
    ]
    triplets = [
        SimpleNamespace(subject="家化新生儿病房", relation="PROVIDES", object="新生儿", confidence=0.9, source_chunk_id="chunk-1", properties={}),
        SimpleNamespace(subject="家化新生儿病房", relation="PROVIDES", object="新生儿", confidence=0.88, source_chunk_id="chunk-2", properties={}),
    ]

    module._write_to_neo4j(
        triplets,
        ea_pairs,
        merge_map={},
        checkpoint_state={"substage": "neo4j_write", "final_triplets": module._serialize_triplets(triplets)},
    )

    assert module.kg_builder.classify_calls == 1


def test_guides_relation_semantic_rule_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"GUIDES": ({"设计方法"}, {"空间", "功能分区", "部门", "医院"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="模块化设计",
        subj_type="医院",
        relation="GUIDES",
        obj="门诊大厅",
        obj_type="医院",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_guides_allow"}


def test_relates_to_method_pair_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"RELATES_TO": ({"设计方法"}, {"设计方法"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="模块化设计",
        subj_type="医院",
        relation="RELATES_TO",
        obj="弹性设计",
        obj_type="空间",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_relates_to_allow"}


def test_relates_to_non_method_pair_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"RELATES_TO": ({"设计方法"}, {"设计方法"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="控制室",
        subj_type="空间",
        relation="RELATES_TO",
        obj="配电柜",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_relates_to_non_method_reject"}


def test_contains_method_category_to_method_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"CONTAINS": ({"医院"}, {"医院"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
        _looks_like_attribute_fragment=MedicalKGBuilder._looks_like_attribute_fragment,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="表格",
        subj_type="设计方法分类",
        relation="CONTAINS",
        obj="技术指标",
        obj_type="设计方法",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_contains_method_category_allow"}


def test_contains_space_to_quantitative_fragment_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"CONTAINS": ({"医院"}, {"医院"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
        _looks_like_attribute_fragment=MedicalKGBuilder._looks_like_attribute_fragment,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="净尺寸",
        subj_type="设计方法",
        relation="CONTAINS",
        obj="面积:40m²",
        obj_type=None,
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_contains_attribute_fragment_reject"}


def test_references_source_pair_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"REFERENCES": ({"资料来源"}, {"资料来源"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="医院建筑设计指南",
        subj_type="医院",
        relation="REFERENCES",
        obj="GB51039-2014综合医院建筑设计标准",
        obj_type="医院",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_references_allow"}


def test_uses_method_to_equipment_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"USES": ({"治疗方法"}, {"医疗设备"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="血液透析治疗",
        subj_type="空间",
        relation="USES",
        obj="透析机",
        obj_type="空间",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_uses_allow"}


def test_uses_space_to_equipment_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"USES": ({"治疗方法"}, {"医疗设备"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="药房",
        subj_type="空间",
        relation="USES",
        obj="门禁系统",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_uses_space_allow"}


def test_requires_zone_to_space_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"REQUIRES": ({"空间", "功能分区", "医疗服务"}, {"空间", "功能分区", "医疗设备"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="手术部",
        subj_type="医院",
        relation="REQUIRES",
        obj="手术室",
        obj_type="医院",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_requires_allow"}


def test_supports_equipment_to_equipment_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"SUPPORTS": ({"医疗设备", "空间"}, {"治疗方法"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="配电柜",
        subj_type="医疗设备",
        relation="SUPPORTS",
        obj="回旋加速器",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_supports_non_method_reject"}


def test_supports_equipment_to_method_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"SUPPORTS": ({"医疗设备", "空间"}, {"治疗方法"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="透析机",
        subj_type="医院",
        relation="SUPPORTS",
        obj="血液透析治疗",
        obj_type="医院",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_supports_allow"}


def test_provides_equipment_to_equipment_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"PROVIDES": ({"部门", "功能分区", "空间"}, {"医疗服务"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="回旋加速器",
        subj_type="医疗设备",
        relation="PROVIDES",
        obj="医疗设备",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_provides_non_service_reject"}


def test_connected_to_equipment_to_equipment_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"CONNECTED_TO": ({"空间"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="初洗池",
        subj_type="医疗设备",
        relation="CONNECTED_TO",
        obj="自动清洗机",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_connected_to_equipment_allow"}


def test_connected_to_space_to_equipment_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"CONNECTED_TO": ({"空间"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="储源室",
        subj_type="空间",
        relation="CONNECTED_TO",
        obj="桥架",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_connected_to_space_equipment_allow"}


def test_adjacent_to_equipment_to_equipment_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"ADJACENT_TO": ({"空间"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="离心机",
        subj_type="医疗设备",
        relation="ADJACENT_TO",
        obj="生物显微镜",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_adjacent_to_equipment_allow"}


def test_performed_in_space_to_case_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"PERFORMED_IN": ({"治疗方法", "医疗服务"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="PCR实验室",
        subj_type="空间",
        relation="PERFORMED_IN",
        obj="案例研究",
        obj_type="案例",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_performed_in_reject"}


def test_guides_method_to_method_category_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"GUIDES": ({"治疗方法"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="地面材料",
        subj_type="设计方法",
        relation="GUIDES",
        obj="装修",
        obj_type="设计方法分类",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_guides_method_category_allow"}


def test_guides_equipment_to_method_category_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"GUIDES": ({"治疗方法"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="储物柜",
        subj_type="医疗设备",
        relation="GUIDES",
        obj="安全私密",
        obj_type="设计方法分类",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_guides_non_method_reject"}


def test_semantic_entity_level_treats_mep_and_furniture_as_equipment_like():
    assert MedicalKGBuilder._semantic_entity_level("照明", "强电") == "equipment"
    assert MedicalKGBuilder._semantic_entity_level("水槽", "家具") == "equipment"


def test_requires_space_to_mep_system_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"REQUIRES": ({"空间", "功能分区", "医疗服务"}, {"空间", "功能分区", "医疗设备"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="4级洁净用房",
        subj_type="空间",
        relation="REQUIRES",
        obj="照明",
        obj_type="强电",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_requires_allow"}


def test_contains_space_to_furniture_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"CONTAINS": ({"空间"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
        _looks_like_attribute_fragment=MedicalKGBuilder._looks_like_attribute_fragment,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="PCR实验室",
        subj_type="空间",
        relation="CONTAINS",
        obj="水槽",
        obj_type="家具",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_contains_reject"}


def test_uses_space_to_method_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"USES": ({"治疗方法"}, {"医疗设备"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="扫描间",
        subj_type="空间",
        relation="USES",
        obj="墙地面装修",
        obj_type="设计方法",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_uses_non_equipment_reject"}


def test_supports_method_to_equipment_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"SUPPORTS": ({"医疗设备", "空间"}, {"治疗方法"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="给排水",
        subj_type="设计方法",
        relation="SUPPORTS",
        obj="医疗设备",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_supports_non_method_reject"}


def test_references_method_to_source_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"REFERENCES": ({"资料来源"}, {"资料来源"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="机电要求",
        subj_type="设计方法",
        relation="REFERENCES",
        obj="规范",
        obj_type="资料来源",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_references_reject"}


def test_is_type_of_equipment_to_equipment_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"IS_TYPE_OF": ({"设计方法"}, {"设计方法分类"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="门禁系统",
        subj_type="医疗设备",
        relation="IS_TYPE_OF",
        obj="医疗设备",
        obj_type="医疗设备",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_is_type_of_subject_reject"}


def test_relates_to_attribute_fragment_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"RELATES_TO": ({"设计方法"}, {"设计方法"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
        _looks_like_attribute_fragment=MedicalKGBuilder._looks_like_attribute_fragment,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="药房",
        subj_type="空间",
        relation="RELATES_TO",
        obj="面积:15m",
        obj_type="设计方法",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_relates_to_attribute_fragment_reject"}


def test_contains_equipment_to_attribute_text_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"CONTAINS": ({"空间"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
        _looks_like_attribute_fragment=MedicalKGBuilder._looks_like_attribute_fragment,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="液晶电视",
        subj_type="医疗设备",
        relation="CONTAINS",
        obj="壁挂式",
        obj_type=None,
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_contains_equipment_reject"}


def test_contains_hospital_to_equipment_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"CONTAINS": ({"医院"}, {"空间"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
        _looks_like_attribute_fragment=MedicalKGBuilder._looks_like_attribute_fragment,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="医院",
        subj_type="医院",
        relation="CONTAINS",
        obj="饮水机",
        obj_type="设备",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_contains_reject"}


def test_requires_equipment_subject_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"REQUIRES": ({"空间", "功能分区", "医疗服务"}, {"空间", "功能分区", "医疗设备"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="回旋加速器",
        subj_type="医疗设备",
        relation="REQUIRES",
        obj="温度控制",
        obj_type="暖通",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_requires_reject"}


def test_requires_space_to_method_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"REQUIRES": ({"空间", "功能分区", "医疗服务"}, {"空间", "功能分区", "医疗设备"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="病房区",
        subj_type="空间",
        relation="REQUIRES",
        obj="隔帘",
        obj_type="设计方法",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_requires_non_dependency_reject"}


def test_is_type_of_space_to_service_is_rejected_without_llm():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"IS_TYPE_OF": ({"设计方法"}, {"设计方法分类"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="急诊诊室",
        subj_type="空间",
        relation="IS_TYPE_OF",
        obj="一般诊疗",
        obj_type="功能分区",
        context="",
    )

    assert result == {"action": "reject", "reason": "semantic_is_type_of_subject_reject"}


def test_is_type_of_method_to_method_category_can_skip_llm_review():
    fake_builder = SimpleNamespace(
        type_synonyms={},
        relation_constraints={"IS_TYPE_OF": ({"设计方法"}, {"设计方法分类"})},
        relation_llm_fallback=True,
        _semantic_entity_level=MedicalKGBuilder._semantic_entity_level,
    )

    result = MedicalKGBuilder._classify_relation_verification_need(
        fake_builder,
        subj="模块化设计",
        subj_type="医院",
        relation="IS_TYPE_OF",
        obj="设计方法分类",
        obj_type="医院",
        context="",
    )

    assert result == {"action": "accept", "reason": "semantic_is_type_of_allow"}


def test_kg_module_applies_neo4j_write_tuning_from_env(monkeypatch):
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))
    module.NEO4J_WRITE_CHECKPOINT_EVERY = 100
    module.NEO4J_WRITE_BATCH_SIZE = 100
    module.NEO4J_VERIFY_BATCH_SIZE = 50

    monkeypatch.setenv("KG_NEO4J_WRITE_CHECKPOINT_EVERY", "250")
    monkeypatch.setenv("KG_NEO4J_WRITE_BATCH_SIZE", "180")
    monkeypatch.setenv("KG_NEO4J_VERIFY_BATCH_SIZE", "120")

    module._apply_neo4j_write_tuning_from_env()

    assert module.NEO4J_WRITE_CHECKPOINT_EVERY == 250
    assert module.NEO4J_WRITE_BATCH_SIZE == 180
    assert module.NEO4J_VERIFY_BATCH_SIZE == 120


def test_kg_module_uses_more_aggressive_neo4j_write_defaults():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"confirmed_merges": []}))

    assert module.NEO4J_WRITE_CHECKPOINT_EVERY >= 100
    assert module.NEO4J_WRITE_BATCH_SIZE in {0, 100}
    assert module.NEO4J_VERIFY_BATCH_SIZE >= 50


def test_build_kg_passes_stage4_checkpoint_into_cross_document_fusion():
    cache = FakeRuntimeCacheCollection()
    module = _make_module(cache, SimpleNamespace(chat_json=lambda *args, **kwargs: {"merges": {}}))
    chunks = [{"chunk_id": "chunk-11", "content_type": "text", "content": LONG_TEXT}]
    captured = {}

    def fail_stage1(*args, **kwargs):
        raise AssertionError("stage1 should be skipped when resume artifacts exist")

    def fail_stage2(*args, **kwargs):
        raise AssertionError("stage2 should be skipped when resume artifacts exist")

    def fake_stage4(triplets, ea_pairs, progress_callback=None, resume_artifacts=None, checkpoint_callback=None):
        captured["resume_artifacts"] = resume_artifacts
        return SimpleNamespace(
            stage="cross_document_fusion",
            rounds=1,
            triplets=triplets,
            stats={"nodes_written": 2, "edges_written": 1, "resumed_substage": resume_artifacts.get("substage")},
        )

    module.stage1_ea_recognition = fail_stage1
    module.stage2_relation_extraction = fail_stage2
    module.stage4_cross_document_fusion = fake_stage4
    module._compute_quality_metrics = lambda triplets: {"triplet_count": len(triplets)}

    module.build_kg(
        chunks,
        resume_artifacts={
            "resume_from_stage": "cross_document_fusion",
            "ea_pairs": [
                {
                    "entity_name": "门诊部",
                    "entity_type": "功能分区",
                    "description": "门诊区域",
                    "attributes": [],
                }
            ],
            "triplets": [
                {
                    "subject": "门诊部",
                    "relation": "CONTAINS",
                    "object": "医院主入口",
                    "confidence": 0.91,
                    "source_chunk_id": "chunk-11",
                    "properties": {},
                }
            ],
            "stage4_checkpoint": {
                "substage": "neo4j_write",
                "final_triplets": [
                    {
                        "subject": "门诊部",
                        "relation": "CONTAINS",
                        "object": "医院主入口",
                        "confidence": 0.91,
                        "source_chunk_id": "chunk-11",
                        "properties": {},
                    }
                ],
                "write_progress": {"processed_count": 1, "verification_results": {}},
            },
        },
    )

    assert captured["resume_artifacts"]["substage"] == "neo4j_write"
