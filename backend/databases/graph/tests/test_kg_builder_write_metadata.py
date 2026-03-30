import sys
import threading
from pathlib import Path

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.databases.graph.builders.kg_builder import MedicalKGBuilder


class _FakeSession:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **kwargs):
        self.calls.append({"query": query, "kwargs": kwargs})
        return _FakeResult()


class _FakeResult:
    def single(self):
        return {"c": 0}


class _FakeDriver:
    def __init__(self, calls):
        self.calls = calls

    def session(self, database=None):
        return _FakeSession(self.calls)


class _FakeCollection:
    def delete_one(self, *_args, **_kwargs):
        return None


def _make_builder_for_write_test():
    builder = MedicalKGBuilder.__new__(MedicalKGBuilder)
    builder.graph = nx.MultiDiGraph()
    builder.keep_attributes_list = False
    builder.neo4j_database = "neo4j"
    builder.neo4j_driver_calls = []
    builder.neo4j_driver = _FakeDriver(builder.neo4j_driver_calls)
    builder.type_to_label = {
        "空间": "Space",
        "资料来源": "Source",
    }
    builder.allowed_props_by_label = {
        "Space": {"name", "description"},
        "Source": {"title", "source_type", "credibility", "doc_ids"},
    }
    builder.primary_name_key_by_label = {
        "Space": "name",
        "Source": "title",
    }
    builder.allowed_rel_props_by_type = {
        "MENTIONED_IN": {
            "perspective",
            "summary",
            "quote",
            "media_refs",
            "page",
            "is_compliance",
        },
        "CONNECTED_TO": {"door_type"},
    }
    builder._auto_attach_orphan_entities = lambda: 0
    return builder


def _relation_rows(calls, relation_name):
    marker = f"MERGE (a)-[e:`{relation_name}`]->(b)"
    for call in calls:
        if marker in call["query"]:
            return call["kwargs"]["rows"]
    raise AssertionError(f"missing write call for relation {relation_name}")


def test_write_to_neo4j_preserves_relation_metadata():
    builder = _make_builder_for_write_test()

    builder.graph.add_node(
        "space_1",
        properties={
            "name": "手术间",
            "schema_type": "空间",
            "chunk_ids": ["chunk-1"],
            "attributes": [],
            "description": "核心手术空间",
        },
        level=2,
    )
    builder.graph.add_node(
        "space_2",
        properties={
            "name": "刷手间",
            "schema_type": "空间",
            "chunk_ids": ["chunk-2"],
            "attributes": [],
            "description": "术前准备空间",
        },
        level=2,
    )
    builder.graph.add_node(
        "source_1",
        properties={
            "title": "GB 51039-2014",
            "schema_type": "资料来源",
            "source_type": "规范标准",
            "credibility": 0.95,
            "chunk_ids": ["chunk-1"],
            "doc_ids": ["doc-1"],
        },
        level=2,
    )

    builder.graph.add_edge(
        "space_1",
        "source_1",
        relation="MENTIONED_IN",
        chunk_id="chunk-1",
        original_relation="MENTIONED_IN",
        confidence=0.9,
        perspective="规范要求",
        summary="规范描述手术间配置要求",
        quote="手术间应满足洁净要求。",
        media_refs=["图1-1"],
        page=12,
        is_compliance=True,
    )
    builder.graph.add_edge(
        "space_1",
        "space_2",
        relation="CONNECTED_TO",
        chunk_id="chunk-2",
        original_relation="CONNECTED_TO",
        confidence=0.7,
        inferred=True,
        support=3,
        window=2,
        reason="cross-chunk cooccurrence",
        refined_by="rule",
        door_type="气密门",
    )

    builder.write_to_neo4j()

    mentioned_rows = _relation_rows(builder.neo4j_driver_calls, "MENTIONED_IN")
    mentioned_props = mentioned_rows[0]["props"]
    assert mentioned_props["perspective"] == "规范要求"
    assert mentioned_props["summary"] == "规范描述手术间配置要求"
    assert mentioned_props["quote"] == "手术间应满足洁净要求。"
    assert mentioned_props["media_refs"] == ["图1-1"]
    assert mentioned_props["page"] == 12
    assert mentioned_props["is_compliance"] is True

    connected_rows = _relation_rows(builder.neo4j_driver_calls, "CONNECTED_TO")
    connected_props = connected_rows[0]["props"]
    assert connected_props["inferred"] is True
    assert connected_props["support"] == 3
    assert connected_props["window"] == 2
    assert connected_props["reason"] == "cross-chunk cooccurrence"
    assert connected_props["refined_by"] == "rule"
    assert connected_props["door_type"] == "气密门"


def test_write_to_databases_merges_confirmed_fusions_before_write():
    builder = _make_builder_for_write_test()
    builder.enable_semantic_fusion = True
    builder.auto_alias_links = False
    builder.enable_cooccurrence_aug = False
    builder.fusion_ratio = 0.9
    builder.fusion_max_pairs = 10
    builder.llm_client = object()
    builder.last_write_summary = {}
    builder.alias_map = {}
    builder._chunk_entity_index = {}
    builder._merge_unique_list = MedicalKGBuilder._merge_unique_list.__get__(
        builder, MedicalKGBuilder
    )
    builder._merge_entity_properties = MedicalKGBuilder._merge_entity_properties.__get__(
        builder, MedicalKGBuilder
    )
    builder._merge_graph_nodes = MedicalKGBuilder._merge_graph_nodes.__get__(
        builder, MedicalKGBuilder
    )
    builder._rewrite_chunk_entity_index = MedicalKGBuilder._rewrite_chunk_entity_index.__get__(
        builder, MedicalKGBuilder
    )

    builder.graph.add_node(
        "space_1",
        properties={
            "name": "重症监护病房",
            "schema_type": "空间",
            "chunk_ids": ["chunk-1"],
            "attributes": [],
        },
        level=2,
    )
    builder.graph.add_node(
        "space_2",
        properties={
            "name": "重症监护室",
            "schema_type": "空间",
            "chunk_ids": ["chunk-2"],
            "attributes": [],
        },
        level=2,
    )

    builder._optimize_triples_stage3 = lambda: {}
    builder.suggest_entity_fusions = lambda ratio, max_pairs: [
        {
            "a": {"id": "space_1", "name": "重症监护病房", "type": "空间"},
            "b": {"id": "space_2", "name": "重症监护室", "type": "空间"},
            "score": 0.95,
            "context": "rel_overlap=1",
        }
    ]
    builder._llm_confirm_fusions = lambda suggestions: [
        {
            **suggestions[0],
            "canonical_name": "重症监护室",
            "llm_confirmed": True,
        }
    ]
    builder.augment_relations_by_rules = lambda: None

    write_snapshots = {}

    def _fake_write():
        write_snapshots["node_names"] = sorted(
            node_data.get("properties", {}).get("name", "")
            for _, node_data in builder.graph.nodes(data=True)
        )

    builder.write_to_neo4j = _fake_write

    builder.write_to_databases()

    assert write_snapshots["node_names"] == ["重症监护室"]
    remaining_names = sorted(
        node_data.get("properties", {}).get("name", "")
        for _, node_data in builder.graph.nodes(data=True)
    )
    assert remaining_names == ["重症监护室"]


def test_add_single_triple_reuses_cached_llm_relation_verification():
    builder = MedicalKGBuilder.__new__(MedicalKGBuilder)
    builder.graph = nx.MultiDiGraph()
    builder.allowed_relation_types = {"CONTAINS"}
    builder.drop_uncertain_relations = False
    builder.relation_constraints = {"CONTAINS": ({"医院"}, {"部门"})}
    builder.type_synonyms = {}
    builder.relation_llm_fallback = True
    builder.chunk_doc_map = {}
    builder._relation_verification_cache = {}
    builder._find_or_create_entity = lambda entity_name, *args, **kwargs: entity_name
    builder._infer_scope_chain = lambda *args, **kwargs: []

    verify_calls = {"count": 0}

    def _verify(*_args, **_kwargs):
        verify_calls["count"] += 1
        return True

    builder._llm_verify_relation = _verify

    entity_types = {"设计方法分类": "设计方法", "透析机": "医疗设备"}

    first = MedicalKGBuilder._add_single_triple_to_graph(
        builder,
        ["设计方法分类", "CONTAINS", "透析机"],
        chunk_id="chunk-1",
        entity_types=entity_types,
        entity_descriptions={},
    )
    second = MedicalKGBuilder._add_single_triple_to_graph(
        builder,
        ["设计方法分类", "CONTAINS", "透析机"],
        chunk_id="chunk-2",
        entity_types=entity_types,
        entity_descriptions={},
    )

    assert first["verification_used"] is True
    assert second["verification_used"] is True
    assert verify_calls["count"] == 1


def test_classify_relation_verification_need_expands_contains_rules():
    builder = MedicalKGBuilder.__new__(MedicalKGBuilder)
    builder.type_synonyms = {}
    builder.relation_llm_fallback = True
    builder.relation_constraints = {"CONTAINS": ({"医院"}, {"部门"})}

    accept = MedicalKGBuilder._classify_relation_verification_need(
        builder,
        subj="综合医院",
        subj_type="医院",
        relation="CONTAINS",
        obj="ICU",
        obj_type="功能分区",
        context="",
    )
    reject = MedicalKGBuilder._classify_relation_verification_need(
        builder,
        subj="设计方法分类",
        subj_type="设计方法",
        relation="CONTAINS",
        obj="手术间",
        obj_type="空间",
        context="",
    )

    assert accept == {"action": "accept", "reason": "semantic_contains_allow"}
    assert reject == {"action": "reject", "reason": "semantic_contains_reject"}


def test_classify_relation_verification_need_rejects_page_reference_mentions():
    builder = MedicalKGBuilder.__new__(MedicalKGBuilder)
    builder.type_synonyms = {}
    builder.relation_llm_fallback = True
    builder.relation_constraints = {"MENTIONED_IN": ({"资料来源", "医院", "人物"}, {"资料来源"})}

    reject = MedicalKGBuilder._classify_relation_verification_need(
        builder,
        subj="hospitalsinachangingeurope",
        subj_type="资料来源",
        relation="MENTIONED_IN",
        obj="p.41",
        obj_type=None,
        context="",
    )

    assert reject == {"action": "reject", "reason": "page_reference_target"}


def test_process_chunk_accepts_entity_only_extraction():
    builder = MedicalKGBuilder.__new__(MedicalKGBuilder)
    builder.lock = threading.Lock()
    builder.link_images = False
    builder.extraction_version = "test-version"
    builder.extractions_collection = _FakeCollection()

    cache_calls = []
    entity_calls = []
    register_calls = []

    builder._hash_text = MedicalKGBuilder._hash_text.__get__(builder, MedicalKGBuilder)
    builder._get_cached_extraction = lambda chunk_id: None
    builder._extract_entities_multi_round = lambda content, content_type: {
        "entities": {
            "分诊台": {"type": "空间", "description": "急诊入口附近的分诊空间"}
        },
        "attributes": {},
        "triples": [],
    }
    builder._filter_and_normalize_entities = (
        lambda raw_entity_types, content, chunk_id: {"分诊台": "空间"}
    )
    builder._cache_extraction_result = (
        lambda chunk_id, extracted, status="success": cache_calls.append(
            (chunk_id, extracted, status)
        )
    )
    builder._infer_scope_chain = lambda name, entity_types: []
    builder._find_or_create_entity = (
        lambda name, chunk_id, entity_type, description, scope_chain=None: entity_calls.append(
            (name, chunk_id, entity_type, description, scope_chain)
        )
        or "entity_triage"
    )
    builder._add_attributes_to_graph = lambda *args, **kwargs: None
    builder._add_triples_to_graph = lambda *args, **kwargs: None
    builder._register_chunk_entities = (
        lambda chunk_id, entity_types, chunk, entity_node_ids=None: register_calls.append(
            (chunk_id, entity_types, entity_node_ids)
        )
    )

    chunk = {
        "chunk_id": "chunk-entity-only",
        "content": "急诊大厅入口处设置分诊台，用于患者初筛与导向。",
        "content_type": "text",
    }

    assert builder.process_chunk(chunk) is True
    assert entity_calls == [
        ("分诊台", "chunk-entity-only", "空间", "急诊入口附近的分诊空间", [])
    ]
    assert register_calls == [
        (
            "chunk-entity-only",
            {"分诊台": "空间"},
            {"分诊台": "entity_triage"},
        )
    ]
    assert cache_calls and cache_calls[0][0] == "chunk-entity-only"


def test_evidence_aggregation_max_pools_confidence():
    """Same triplet from two chunks should merge: max confidence, union chunk_ids, union doc_ids."""
    builder = _make_builder_for_write_test()

    for nid, name, cid in [("s1", "Operating Room", "c1"), ("s2", "Scrub Room", "c2")]:
        builder.graph.add_node(nid, properties={
            "name": name, "schema_type": "\u7a7a\u95f4",
            "chunk_ids": [cid], "attributes": [], "description": "",
        }, level=2)

    # Same triplet from chunk-1 (confidence 0.7)
    builder.graph.add_edge("s1", "s2",
        relation="CONNECTED_TO", chunk_id="c1", chunk_ids=["c1"],
        original_relation="CONNECTED_TO", confidence=0.7,
        support_doc_ids=["doc-A"],
    )
    # Same triplet from chunk-2 (confidence 0.9)
    builder.graph.add_edge("s1", "s2",
        relation="CONNECTED_TO", chunk_id="c2", chunk_ids=["c2"],
        original_relation="CONNECTED_TO", confidence=0.9,
        support_doc_ids=["doc-B"],
    )

    builder.write_to_neo4j()

    rows = _relation_rows(builder.neo4j_driver_calls, "CONNECTED_TO")
    # Should be exactly 1 merged row
    assert len(rows) == 1, f"Expected 1 merged row, got {len(rows)}"
    props = rows[0]["props"]
    # Max-pooled confidence
    assert props["confidence"] == 0.9
    # Union of chunk_ids
    assert sorted(props["chunk_ids"]) == ["c1", "c2"]
    # Union of doc_ids
    assert sorted(props["support_doc_ids"]) == ["doc-A", "doc-B"]
    # Support count
    assert props["support_count"] == 2


def test_latent_relation_discovery_adds_inferred_edges():
    """Entity pairs sharing docs but no direct relation should get LLM-inferred edges."""
    builder = _make_builder_for_write_test()
    builder.llm_client = type("FakeLLM", (), {
        "chat_json": lambda self, **kw: [
            {"entity_a": "Emergency Dept", "entity_b": "Triage Area",
             "has_relation": True, "relation": "CONTAINS", "reason": "triage is part of ED"}
        ]
    })()
    builder.allowed_relation_types = {
        "CONTAINS", "ADJACENT_TO", "CONNECTED_TO", "REQUIRES", "PROVIDES",
        "PERFORMED_IN", "USES", "SUPPORTS", "GUIDES", "MENTIONED_IN",
    }
    builder.chunk_doc_map = {"c1": "doc-A", "c2": "doc-A"}
    builder._chunk_entity_index = {
        "c1": {"Emergency Dept": "s1"},
        "c2": {"Triage Area": "s2"},
    }

    builder.graph.add_node("s1", properties={
        "name": "Emergency Dept", "schema_type": "FunctionalZone",
        "chunk_ids": ["c1"], "attributes": [],
    }, level=2)
    builder.graph.add_node("s2", properties={
        "name": "Triage Area", "schema_type": "Space",
        "chunk_ids": ["c2"], "attributes": [],
    }, level=2)
    # No edge between s1 and s2

    builder.discover_latent_relations(max_pairs=10, batch_size=5)

    # Should have added an inferred edge (direction may vary due to set iteration)
    all_edges = list(builder.graph.edges(data=True))
    inferred = [d for _, _, d in all_edges if d.get("inferred") is True]
    assert len(inferred) >= 1, f"Expected at least 1 inferred edge, got {len(inferred)}"
    edge_data = inferred[0]
    assert edge_data["relation"] == "CONTAINS"
    assert "doc-A" in (edge_data.get("support_doc_ids") or [])


def test_standard_relations_covers_schema():
    """STANDARD_RELATIONS must include all 14 schema-defined relation types."""
    from backend.databases.graph.builders.relation_mapping import STANDARD_RELATIONS

    schema_relations = {
        "MENTIONED_IN", "CONTAINS", "CONNECTED_TO", "ADJACENT_TO",
        "REQUIRES", "PROVIDES", "PERFORMED_IN", "USES", "SUPPORTS",
        "GUIDES", "IS_TYPE_OF", "RELATES_TO", "REFERENCES", "REFERS_TO",
    }
    missing = schema_relations - set(STANDARD_RELATIONS)
    assert not missing, f"STANDARD_RELATIONS missing schema types: {missing}"
