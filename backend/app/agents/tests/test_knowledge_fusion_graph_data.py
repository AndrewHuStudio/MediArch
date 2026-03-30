from pathlib import Path
import sys
import hashlib

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agents.base_agent import AgentItem
from backend.app.agents.knowledge_fusion import build_answer_graph_data


def test_build_answer_graph_data_merges_source_documents_from_neo4j_and_milvus():
    source_name = "医院建筑设计指南.pdf"
    doc_id = f"doc_{hashlib.md5(source_name.encode('utf-8')).hexdigest()[:10]}"

    neo4j_items = [
        AgentItem(
            entity_id="space_ward",
            name="病房",
            label="Space",
            edges=[
                {
                    "type": "MENTIONED_IN",
                    "target": source_name,
                }
            ],
            source="neo4j_agent",
        )
    ]
    milvus_items = [
        AgentItem(
            entity_id="chunk_1",
            name="病房设计要点",
            attrs={"source_document": source_name},
            citations=[{"source": source_name, "chunk_id": "chunk_1"}],
            source="milvus_agent",
        )
    ]

    graph_data = build_answer_graph_data(neo4j_items, milvus_items, query="病房")

    source_nodes = [node for node in graph_data.nodes if node.name == source_name]
    assert len(source_nodes) == 1
    assert source_nodes[0].id == doc_id
    assert source_nodes[0].type == "Source"

    mentioned_edges = [
        edge for edge in graph_data.edges
        if edge.relation == "MENTIONED_IN" and edge.target == doc_id
    ]
    assert any(edge.source == "space_ward" for edge in mentioned_edges)
    assert any(edge.source.startswith("kp_") for edge in mentioned_edges)


def test_build_answer_graph_data_uses_snippet_for_fallback_knowledge_point_when_item_name_is_source_doc():
    source_name = "医院建筑设计指南.pdf"

    milvus_items = [
        AgentItem(
            entity_id="chunk_2",
            name=source_name,
            attrs={
                "source_document": source_name,
                "section": "护理单元",
            },
            citations=[
                {
                    "source": source_name,
                    "chunk_id": "chunk_2",
                    "snippet": "护理单元应兼顾护士站观察效率与病房空间合规。",
                    "section": "护理单元",
                }
            ],
            snippet="护理单元应兼顾护士站观察效率与病房空间合规。",
            source="milvus_agent",
        )
    ]

    graph_data = build_answer_graph_data([], milvus_items, query="护理单元")

    named_as_source = [node for node in graph_data.nodes if node.name == source_name]
    assert len(named_as_source) == 1
    assert named_as_source[0].type == "Source"

    knowledge_nodes = [node for node in graph_data.nodes if node.type == "KnowledgePoint"]
    assert len(knowledge_nodes) == 1
    assert knowledge_nodes[0].name != source_name
    assert "护理单元" in knowledge_nodes[0].name


def test_build_answer_graph_data_adds_synthetic_bridge_edges_between_components():
    source_name = "医院建筑设计指南.pdf"
    snippet = "导诊机器人可提升门诊导向效率并降低问询压力。"
    expected_kp_id = f"kp_{hashlib.md5((snippet + source_name).encode('utf-8')).hexdigest()[:10]}"

    neo4j_items = [
        AgentItem(
            entity_id="equipment_guide_robot",
            name="导诊机器人",
            label="MedicalEquipment",
            source="neo4j_agent",
        )
    ]
    milvus_items = [
        AgentItem(
            entity_id="chunk_3",
            name=source_name,
            attrs={"source_document": source_name},
            citations=[
                {
                    "source": source_name,
                    "chunk_id": "chunk_3",
                    "snippet": snippet,
                }
            ],
            snippet=snippet,
            source="milvus_agent",
        )
    ]

    graph_data = build_answer_graph_data(neo4j_items, milvus_items, query="")

    bridge_edges = [
        edge for edge in graph_data.edges
        if edge.relation == "BRIDGED_TO"
    ]

    assert len(bridge_edges) == 1
    assert bridge_edges[0].properties["synthetic"] is True
    assert bridge_edges[0].properties["visual_bridge"] is True
    assert bridge_edges[0].properties["reason"] == "connect_components"
    assert {bridge_edges[0].source, bridge_edges[0].target} == {"equipment_guide_robot", expected_kp_id}
