from backend.app.agents.base_agent import AgentItem, AgentRequest
from backend.app.agents.knowledge_fusion.fusion import build_unified_hints
from backend.app.agents.mongodb_agent import agent as mongodb_agent
from backend.app.agents.result_synthesizer_agent import agent as synthesizer_agent

import asyncio


def test_neo4j_source_nodes_are_exposed_as_unified_source_documents():
    """KG 图谱里的 Source 节点必须进入 MongoDB 可用的 source_documents hints。"""
    neo4j_items = [
        AgentItem(
            entity_id="space-outpatient",
            name="诊室",
            label="Space",
            source="neo4j_agent",
            edges=[
                {
                    "type": "MENTIONED_IN",
                    "target": "医院建筑设计指南.pdf",
                    "target_label": "Source",
                },
                {
                    "type": "MENTIONED_IN",
                    "target": "GB 51039-2014 综合医院建筑设计规范.pdf",
                    "target_label": "Source",
                },
            ],
        )
    ]

    hints = build_unified_hints(neo4j_items, [], query="门诊单元如何设计")

    assert "医院建筑设计指南.pdf" in hints.source_documents
    assert "GB 51039-2014 综合医院建筑设计规范.pdf" in hints.source_documents


def test_mongodb_supplements_chunk_id_hits_with_graph_source_documents(monkeypatch):
    """chunk_id 命中单本资料时，仍要按 KG source_documents 补查其它图谱资料源。"""
    calls = {"keyword_sources": []}

    class _Retriever:
        def get_chunks_by_ids(self, _ids):
            return [
                {
                    "chunk_id": "guide-hit",
                    "doc_title": "医院建筑设计指南.pdf",
                    "chunk_text": "门诊单元组织模式。",
                }
            ]

        def smart_keyword_search(self, search_terms, query, top_k, doc_ids=None, source_documents=None):
            calls["keyword_sources"].append(list(source_documents or []))
            if source_documents == ["GB 51039-2014 综合医院建筑设计规范.pdf"]:
                return (
                    [
                        {
                            "chunk_id": "gb-hit",
                            "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
                            "chunk_text": "门诊部用房基本要求。",
                        }
                    ],
                    "source_document_hint",
                    {"attempts": ["source_document_hint"]},
                )
            return [], "none", {"attempts": []}

    monkeypatch.setattr(mongodb_agent, "get_retriever", lambda: _Retriever())

    state = {
        "query": "门诊单元如何设计",
        "search_terms": ["门诊单元", "诊室"],
        "request": AgentRequest(
            query="门诊单元如何设计",
            top_k=6,
            metadata={
                "retrieval_mode": "R2",
                "unified_hints": {
                    "chunk_ids": ["guide-hit"],
                    "source_documents": [
                        "医院建筑设计指南.pdf",
                        "GB 51039-2014 综合医院建筑设计规范.pdf",
                    ],
                },
            },
        ),
    }

    result = asyncio.run(mongodb_agent.node_search_mongodb(state))
    docs = {chunk.get("doc_title") for chunk in result["retrieval_results"]}

    assert docs == {"医院建筑设计指南.pdf", "GB 51039-2014 综合医院建筑设计规范.pdf"}
    assert ["GB 51039-2014 综合医院建筑设计规范.pdf"] in calls["keyword_sources"]
    assert result["diagnostics"]["graph_source_docs_added"] == 1


def test_final_answer_includes_missing_retrieved_source_content_when_llm_cites_only_one():
    """候选证据覆盖多份资料时，缺失资料的检索片段也要进入最终答案。"""
    answer = "门诊单元应组织候诊、咨询、检查和后台支持功能。[1]"
    citations = [
        {"source": "医院建筑设计指南.pdf", "snippet": "门诊单元组织模式。", "chunk_id": "guide-1"},
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "snippet": "门诊部用房基本要求。",
            "chunk_id": "gb-1",
        },
        {"source": "医院洁净手术部建筑技术规范.pdf", "snippet": "旁路资料。", "chunk_id": "other-1"},
    ]

    widened = synthesizer_agent._append_missing_source_evidence_summary(
        answer,
        citations,
        max_sources=12,
    )
    remapped_answer, remapped_citations = synthesizer_agent._remap_citations_by_first_appearance(
        widened,
        citations,
    )

    assert "[1]" in remapped_answer
    assert "[2]" in remapped_answer
    assert "门诊部用房基本要求" in remapped_answer
    assert "补充设计要点" in remapped_answer
    assert "GB 51039-2014 综合医院建筑设计规范.pdf" not in remapped_answer
    assert "医院洁净手术部建筑技术规范.pdf" not in remapped_answer
    assert {c["source"] for c in remapped_citations} == {
        "医院建筑设计指南.pdf",
        "GB 51039-2014 综合医院建筑设计规范.pdf",
    }


def test_complex_design_answer_gets_multisource_design_expansion():
    query = "护士站服务半径和通视隐私如何结合规范、图集和论文进行空间推理并给设计建议？"
    answer = "护士站设计应兼顾服务效率和患者隐私。[1]"
    citations = [
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "snippet": "护理单元布置应满足医疗护理流程和安全疏散要求。",
            "chunk_id": "code-1",
        },
        {
            "source": "医疗功能房间详图集3.pdf",
            "snippet": "护理单元平面示例展示护士站、病房走廊和辅助用房的组织关系。",
            "chunk_id": "atlas-1",
        },
        {
            "source": "护理单元空间组织研究论文.pdf",
            "snippet": "研究指出开放可视的护理核心有利于缩短响应距离，但需要通过界面和视线控制保护患者隐私。",
            "chunk_id": "paper-1",
        },
    ]
    tiers = synthesizer_agent._build_evidence_tiers(citations)

    expanded = synthesizer_agent._append_multisource_design_expansion(
        query=query,
        text=answer,
        citations=citations,
        evidence_tiers=tiers,
    )

    assert "综合设计补充" in expanded
    assert "规范边界" in expanded
    assert "空间/图集做法" in expanded
    assert "研究/案例解释" in expanded
    assert "证据边界" in expanded
    assert "[2]" in expanded
    assert "[3]" in expanded


def test_plain_single_point_answer_does_not_get_design_expansion():
    query = "护士站服务半径是多少？"
    answer = "护士站到最远病房门口距离不宜超过30m。[1]"
    citations = [
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "snippet": "护士站到最远病房门口距离不宜超过30m。",
            "chunk_id": "code-1",
        }
    ]

    expanded = synthesizer_agent._append_multisource_design_expansion(
        query=query,
        text=answer,
        citations=citations,
        evidence_tiers=synthesizer_agent._build_evidence_tiers(citations),
    )

    assert expanded == answer
