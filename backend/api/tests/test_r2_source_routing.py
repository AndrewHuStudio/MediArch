from backend.app.agents.base_agent import AgentItem
from backend.app.agents.base_agent import AgentRequest
from backend.app.agents.base_agent import summarize_worker_responses
from backend.app.agents.mongodb_agent import agent as mongodb_agent
from backend.app.agents.result_synthesizer_agent import agent as synthesizer_agent
from backend.app.agents.evidence_orchestration import build_evidence_plan_for_query

import asyncio


def _item(doc_name: str, *, source: str = "milvus_agent", chunk_id: str = "c1") -> AgentItem:
    return AgentItem(
        entity_id=chunk_id,
        name=doc_name,
        source=source,
        snippet=f"{doc_name} snippet",
        attrs={"source_document": doc_name},
        citations=[
            {
                "source": doc_name,
                "chunk_id": chunk_id,
                "content_type": "text",
                "snippet": f"{doc_name} citation",
            }
        ],
    )


def test_standard_fact_questions_do_not_request_images_by_default():
    query = "根据《综合医院建筑设计规范》GB 51039-2014，出入口和室内净高有哪些基本设计要求？"

    assert synthesizer_agent._wants_images(query) is False
    assert mongodb_agent._want_images(query) is False


def test_explicit_drawing_questions_still_request_images():
    query = "请给出手术室平面图和流线示意图"

    assert synthesizer_agent._wants_images(query) is True
    assert mongodb_agent._want_images(query) is True


def test_explicit_image_negation_disables_images():
    query = "请总结门诊单元设计要求，不需要图片"

    assert synthesizer_agent._wants_images(query) is False
    assert mongodb_agent._want_images(query) is False


def test_normative_queries_prioritize_code_specs_in_document_views():
    query = "根据《综合医院建筑设计规范》GB 51039-2014，推床通道和室内净高有哪些基本要求？"
    items = [
        _item("医院建筑设计指南.pdf", chunk_id="guide"),
        _item("既有大型综合医院门诊部功能布局优化设计研究_呙俊", chunk_id="paper"),
        _item("GB 51039-2014 综合医院建筑设计规范.pdf", chunk_id="gb"),
    ]

    views = synthesizer_agent._build_document_views(items, query=query)

    assert views[0]["doc_name"] == "GB 51039-2014 综合医院建筑设计规范.pdf"
    assert views[0]["role"] == "code_spec"


def test_synthesizer_state_preserves_citation_outputs():
    state_fields = synthesizer_agent.SynthesizerState.__annotations__

    assert "final_citations" in state_fields
    assert "strict_citations_candidate_count" in state_fields


def test_normative_queries_keep_code_spec_documents_first_after_balance():
    query = "根据GB 51039-2014，病房走廊净宽和护士站布置要求是什么"
    items = [
        _item("医疗功能房间详图集3.pdf", chunk_id="atlas"),
        _item("医院建筑设计指南.pdf", chunk_id="guide"),
        _item("GB 51039-2014 综合医院建筑设计规范.pdf", chunk_id="gb"),
    ]

    views = synthesizer_agent._build_document_views(items, query=query)

    assert views[0]["role"] == "code_spec"
    assert "GB" in views[0]["doc_name"]


def test_standards_first_code_spec_is_preferred_without_dropping_supplements():
    query = "护士站服务半径和通视隐私如何满足规范要求？"
    items = [
        _item("医院建筑设计指南.pdf", chunk_id="guide"),
        _item("医疗功能房间详图集3.pdf", chunk_id="atlas"),
        AgentItem(
            entity_id="code",
            name="GB 51039-2014 综合医院建筑设计规范.pdf",
            source="mongodb_agent",
            snippet="护士站到最远病房门口距离不宜超过30m。",
            attrs={"retrieval_lane": "standards_first", "evidence_tier": "code_spec"},
            citations=[
                {
                    "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
                    "chunk_id": "code",
                    "snippet": "护士站到最远病房门口距离不宜超过30m。",
                    "retrieval_lane": "standards_first",
                    "evidence_tier": "code_spec",
                }
            ],
        ),
    ]

    views = synthesizer_agent._build_document_views(items, query=query)
    roles = [view["role"] for view in views]

    assert views[0]["role"] == "code_spec"
    assert views[0]["citations"][0]["retrieval_lane"] == "standards_first"
    assert "guide" in roles
    assert "detail_atlas" in roles


def test_normative_queries_do_not_include_online_supplements_by_default():
    query = "根据GB 51039-2014，病房走廊净宽和护士站布置要求是什么"

    assert synthesizer_agent._should_include_online_supplements(query) is False


def test_implicit_nursing_unit_constraints_route_as_normative_queries():
    query = "在现代住院部设计中，如何通过空间排布满足30米服务半径，同时平衡护士站通视性与患者隐私？"

    assert synthesizer_agent._is_normative_query(query) is True


def test_implicit_numeric_constraints_are_normalized_without_doc_title_injection(monkeypatch):
    async def _no_llm_rewrite(_query):
        return None

    monkeypatch.setattr(mongodb_agent, "rewrite_query_with_llm", _no_llm_rewrite)
    query = "在现代住院部设计中，如何通过空间排布满足30米服务半径，同时平衡护士站通视性与患者隐私？"

    result = asyncio.run(
        mongodb_agent.node_rewrite_query(
            {"query": query, "request": AgentRequest(query=query)}
        )
    )

    terms = result["search_terms"]
    assert "30m" in terms
    assert "30 m" in terms
    assert "GB 51039-2014" not in terms


def test_mongodb_rewrite_adds_role_based_terms_for_required_evidence_lanes(monkeypatch):
    async def _no_llm_rewrite(_query):
        return None

    monkeypatch.setattr(mongodb_agent, "rewrite_query_with_llm", _no_llm_rewrite)
    query = "如何在满足30米服务半径时平衡护士站通视和患者隐私？"

    result = asyncio.run(
        mongodb_agent.node_rewrite_query(
            {"query": query, "request": AgentRequest(query=query)}
        )
    )

    terms = result["search_terms"]
    assert any("规范" in term or "标准" in term for term in terms)
    assert any("图集" in term or "详图" in term for term in terms)
    assert any("指南" in term or "手册" in term for term in terms)
    assert "GB 51039-2014" not in terms


def test_mongodb_rewrite_adds_policy_terms_for_policy_context(monkeypatch):
    async def _no_llm_rewrite(_query):
        return None

    monkeypatch.setattr(mongodb_agent, "rewrite_query_with_llm", _no_llm_rewrite)
    query = "《医疗机构设置规划指导原则（2021-2025年）》明确了医疗机构设置应坚持哪些基本原则？"

    result = asyncio.run(
        mongodb_agent.node_rewrite_query(
            {
                "query": query,
                "request": AgentRequest(
                    query=query,
                    metadata={"retrieval_mode": "R2", "source_type": "policy_document"},
                ),
            }
        )
    )

    terms = result["search_terms"]
    assert any(term in terms for term in ("政策", "规划", "指导原则", "实施方案"))
    assert "规范" not in terms


def test_mongodb_rewrite_adds_book_terms_for_book_context(monkeypatch):
    async def _no_llm_rewrite(_query):
        return None

    monkeypatch.setattr(mongodb_agent, "rewrite_query_with_llm", _no_llm_rewrite)
    query = "根据《医院建筑设计指南》“分区和交通系统”一节，医院建筑通常可分为哪四个功能区？"

    result = asyncio.run(
        mongodb_agent.node_rewrite_query(
            {
                "query": query,
                "request": AgentRequest(
                    query=query,
                    metadata={"retrieval_mode": "R2", "source_type": "book_report"},
                ),
            }
        )
    )

    terms = result["search_terms"]
    assert any(term in terms for term in ("医院建筑设计指南", "书籍", "专著"))
    assert "规范" not in terms


def test_mongodb_rewrite_filters_internal_labels_and_document_filename_noise(monkeypatch):
    async def _no_llm_rewrite(_query):
        return None

    monkeypatch.setattr(mongodb_agent, "rewrite_query_with_llm", _no_llm_rewrite)
    query = "综合医院中，门诊如何设计？"

    result = asyncio.run(
        mongodb_agent.node_rewrite_query(
            {
                "query": query,
                "request": AgentRequest(
                    query=query,
                    metadata={
                        "unified_hints": {
                            "entity_names": [
                                "门诊部",
                                "DesignMethod社区",
                                "GB51039-2014综合医院建筑设计标准.pdf",
                            ],
                            "search_terms": ["Outpatient Department", "医疗工艺", "医院建筑设计指南.pdf"],
                        }
                    },
                ),
            }
        )
    )

    assert "门诊部" in result["search_terms"]
    assert "Outpatient Department" in result["search_terms"]
    assert "DesignMethod社区" not in result["search_terms"]
    assert all(not term.lower().endswith(".pdf") for term in result["search_terms"])


def test_r2_normative_queries_build_standards_first_search_plan():
    query = "护士站服务半径和通视隐私如何满足规范要求？"

    plan = mongodb_agent._build_search_query_plan(
        query,
        ["护士站", "服务半径"],
        AgentRequest(query=query, metadata={"retrieval_mode": "R2"}),
    )

    assert plan[0]["lane"] == "standards_first"
    assert any("规范" in q or "标准" in q or "条文" in q for q in plan[0]["queries"])
    assert any("护士站" in q or "服务半径" in q for q in plan[0]["queries"])
    assert not any("GB 51039" in q for q in plan[0]["queries"])
    assert any(entry["lane"] == "general" and entry["queries"] == [query] for entry in plan)


def test_policy_r2_search_plan_does_not_trigger_standards_first():
    query = "《医疗机构设置规划指导原则（2021-2025年）》明确了医疗机构设置应坚持哪些基本原则？"
    plan = mongodb_agent._build_search_query_plan(
        query,
        ["医疗机构设置规划"],
        AgentRequest(
            query=query,
            metadata={
                "retrieval_mode": "R2",
                "source_type": "policy_document",
                "task_type": "fact",
                "question_id": "Q019",
            },
        ),
    )

    assert not any(entry["lane"] == "standards_first" for entry in plan)
    general = next(entry for entry in plan if entry["lane"] == "general")
    assert general["queries"] == [query]


def test_technical_standard_r2_search_plan_still_triggers_standards_first():
    query = "根据《综合医院建筑设计规范》GB 51039-2014，综合医院基地选址与总平面设计应满足哪些核心要求？"
    plan = mongodb_agent._build_search_query_plan(
        query,
        ["综合医院", "基地选址"],
        AgentRequest(
            query=query,
            metadata={
                "retrieval_mode": "R2",
                "source_type": "technical_standard",
                "task_type": "fact",
                "question_id": "Q006",
            },
        ),
    )

    assert plan[0]["lane"] == "standards_first"
    assert "code_spec" in plan[0]["authority_evidence_need"]["required_roles"]


def test_r2_standards_first_plan_exposes_authority_need_not_question_patch():
    query = "手术部、放射科、磁共振、放疗科和核医学科在选址分区及流线组织上应如何设置？"

    plan = mongodb_agent._build_search_query_plan(
        query,
        ["手术部", "放射科"],
        AgentRequest(query=query, metadata={"retrieval_mode": "R2"}),
    )

    standards = plan[0]
    need = standards["authority_evidence_need"]

    assert standards["lane"] == "standards_first"
    assert need["required_roles"] == ["code_spec"]
    assert "guide" in need["optional_roles"]
    assert "atlas_or_image" in need["optional_roles"]
    assert "核医学" in need["domain_terms"]
    assert "防护分区" in need["constraint_terms"]
    assert "workflow_zoning" in need["claim_scopes"]
    assert all("radiology_department" not in query_text for query_text in standards["queries"])
    assert "question_id" not in need


def test_standards_first_code_spec_chunks_are_marked_for_citations():
    chunks = [
        {
            "chunk_id": "code-1",
            "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "chunk_text": "护士站到最远病房门口距离不宜超过30m。",
            "retrieval_lane": "standards_first",
        },
        {
            "chunk_id": "guide-1",
            "doc_title": "医院建筑设计指南.pdf",
            "chunk_text": "护理单元平面组织。",
            "retrieval_lane": "standards_first",
        },
    ]

    result = asyncio.run(mongodb_agent.node_format_results({"retrieval_results": chunks}))
    citations = [item.citations[0] for item in result["items"]]

    assert citations[0]["retrieval_lane"] == "standards_first"
    assert citations[0]["evidence_tier"] == "code_spec"
    assert citations[1]["retrieval_lane"] == "standards_first"
    assert "evidence_tier" not in citations[1]


def test_standards_first_diagnostics_summarize_hit_quality():
    chunks = [
        {
            "chunk_id": "code-1",
            "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "chunk_text": "护士站到最远病房门口距离不宜超过30m。",
            "retrieval_lane": "standards_first",
            "evidence_tier": "code_spec",
        },
        {
            "chunk_id": "guide-1",
            "doc_title": "医院建筑设计指南.pdf",
            "chunk_text": "护理单元平面组织。",
            "retrieval_lane": "standards_first",
        },
    ]

    diagnostics = mongodb_agent._summarize_standards_first_hits(chunks)

    assert diagnostics["hit_count"] == 2
    assert diagnostics["code_spec_count"] == 1
    assert diagnostics["role_distribution"] == {"code_spec": 1, "guide": 1}
    assert diagnostics["doc_titles"] == [
        "GB 51039-2014 综合医院建筑设计规范.pdf",
        "医院建筑设计指南.pdf",
    ]
    assert diagnostics["code_spec_doc_titles"] == ["GB 51039-2014 综合医院建筑设计规范.pdf"]


def test_standards_first_chunks_are_ranked_by_authority_need_before_merge():
    query = "护士站服务半径和通视隐私如何满足规范要求？"
    plan = mongodb_agent._build_search_query_plan(
        query,
        ["护士站", "服务半径"],
        AgentRequest(query=query, metadata={"retrieval_mode": "R2"}),
    )
    need = plan[0]["authority_evidence_need"]
    chunks = [
        {
            "chunk_id": "guide-1",
            "doc_title": "医院建筑设计指南.pdf",
            "chunk_text": "护士站应结合护理路径和患者隐私组织。",
        },
        {
            "chunk_id": "atlas-1",
            "doc_title": "医疗功能房间详图集3.pdf",
            "content_type": "image",
            "image_url": "/images/nursing-plan.png",
            "chunk_text": "护理单元平面图展示护士站、病房与走廊关系。",
        },
        {
            "chunk_id": "code-1",
            "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "section": "5.5.6",
            "chunk_text": "护士站到最远病房门口的距离不宜超过30m。",
        },
    ]

    ranked = mongodb_agent._rank_standards_first_chunks(chunks, need)

    assert [chunk["chunk_id"] for chunk in ranked] == ["code-1", "atlas-1", "guide-1"]
    assert ranked[0]["retrieval_lane"] == "standards_first"
    assert ranked[0]["evidence_tier"] == "code_spec"
    assert ranked[0]["authority_record"]["claim_scopes"]


def test_missing_evidence_roles_are_selected_from_generic_candidates():
    existing = [
        {"chunk_id": "guide-1", "doc_title": "医院建筑设计指南.pdf", "content_type": "text"},
    ]
    candidates = [
        {"chunk_id": "code-1", "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf", "content_type": "text"},
        {"chunk_id": "atlas-1", "doc_title": "医疗功能房间详图集3.pdf", "content_type": "image"},
        {"chunk_id": "paper-1", "doc_title": "护理单元空间组织研究.pdf", "content_type": "text"},
        {"chunk_id": "guide-2", "doc_title": "医院建筑设计指南.pdf", "content_type": "text"},
    ]

    selected = mongodb_agent._select_missing_evidence_role_chunks(existing, candidates)
    selected_ids = [chunk["chunk_id"] for chunk in selected]

    assert selected_ids == ["code-1", "atlas-1", "paper-1"]


def test_evidence_tiers_classify_multisource_citations_without_flattening():
    citations = [
        {"source": "GB 51039-2014 综合医院建筑设计规范.pdf", "snippet": "护士站到最远病房门口距离不宜超过30m。"},
        {"source": "医院建筑设计指南.pdf", "snippet": "护士站需结合护理路径和观察组织。"},
        {"source": "医疗功能房间详图集3.pdf", "content_type": "image", "snippet": "护理单元平面组织。"},
        {"source": "既有大型综合医院门诊部功能布局优化设计研究_呙俊.pdf", "snippet": "空间布局优化研究。"},
    ]

    tiers = synthesizer_agent._build_evidence_tiers(citations)

    assert [c["source"] for c in tiers["code_spec"]] == ["GB 51039-2014 综合医院建筑设计规范.pdf"]
    assert [c["source"] for c in tiers["guide"]] == ["医院建筑设计指南.pdf"]
    assert [c["source"] for c in tiers["atlas_or_image"]] == ["医疗功能房间详图集3.pdf"]
    assert [c["source"] for c in tiers["paper_or_report"]] == ["既有大型综合医院门诊部功能布局优化设计研究_呙俊.pdf"]
    assert "inference_context" in tiers


def test_synthesizer_llm_quality_evaluator_is_opt_in(monkeypatch):
    monkeypatch.delenv("RESULT_SYNTHESIZER_EVAL_LLM", raising=False)
    assert synthesizer_agent._should_use_llm_quality_evaluator() is False

    monkeypatch.setenv("RESULT_SYNTHESIZER_EVAL_LLM", "1")
    assert synthesizer_agent._should_use_llm_quality_evaluator() is True


def test_worker_response_summary_deduplicates_agents_and_keeps_timings():
    summary = summarize_worker_responses(
        [
            {"agent_name": "milvus_agent", "took_ms": 100, "item_count": 3},
            {"agent_name": "neo4j_agent", "took_ms": 80, "item_count": 2},
            {"agent_name": "milvus_agent", "took_ms": 100, "item_count": 3},
        ]
    )

    assert summary["agents_used"] == ["milvus_agent", "neo4j_agent"]
    assert summary["worker_timings"] == [
        {"agent_name": "milvus_agent", "took_ms": 100, "item_count": 3},
        {"agent_name": "neo4j_agent", "took_ms": 80, "item_count": 2},
    ]


def test_prompt_document_view_is_compact_and_drops_heavy_attrs():
    doc = {
        "doc_name": "医院建筑设计指南.pdf",
        "role": "guide",
        "role_priority": 1,
        "pages": ["81"],
        "locations": ["81页|空间"],
        "item_count": 2,
        "page_span": "P81",
        "highlights": [
            {
                "snippet": "a" * 500,
                "attrs": {"large": "x" * 1000},
                "score": 0.8,
            }
        ],
        "citations": [{"source": "医院建筑设计指南.pdf", "snippet": "heavy"}],
        "images": [{"image_url": "img.png"}],
    }

    compact = synthesizer_agent._compact_document_view_for_prompt(doc)

    assert "citations" not in compact
    assert compact["images_count"] == 1
    assert len(compact["highlights"][0]["snippet"]) <= 481
    assert "attrs" not in compact["highlights"][0]


def test_prompt_citation_is_compact_and_drops_frontend_metadata():
    citation = {
        "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
        "location": "5.5.6",
        "snippet": "b" * 500,
        "positions": [{"page": 1, "bbox": [0, 0, 1, 1]}],
        "pdf_url": "/documents/pdf?path=x",
        "file_path": "标准规范/x.pdf",
        "highlight_text": "h" * 1000,
        "metadata": {"large": "x" * 1000},
        "evidence_tier": "code_spec",
    }

    compact = synthesizer_agent._compact_citation_for_prompt(citation)

    assert compact["source"] == citation["source"]
    assert compact["evidence_tier"] == "code_spec"
    # code_spec 正文给更高字符预算（见 PROMPT_SNIPPET_CHARS_CODE_SPEC），
    # snippet 偏短时回落到更完整的 highlight_text。
    assert len(compact["snippet"]) <= synthesizer_agent.PROMPT_SNIPPET_CHARS_CODE_SPEC + 1
    assert "positions" not in compact
    assert "pdf_url" not in compact
    assert "metadata" not in compact
    assert "highlight_text" not in compact


def test_synthesizer_keeps_required_code_spec_citation_when_final_cap_is_full():
    query = "根据《综合医院建筑设计规范》GB 51039-2014，综合医院基地选址与总平面设计应满足哪些核心要求？"
    _profile, _context, evidence_plan = build_evidence_plan_for_query(
        query,
        {"source_type": "technical_standard", "task_type": "fact", "question_id": "Q006"},
    )
    initial = [
        {
            "source": "医院建筑设计指南.pdf",
            "chunk_id": "guide-1",
            "snippet": "总体布局设计说明。",
            "pdf_url": "/documents/pdf?path=guide.pdf",
        },
        {
            "source": "既有大型综合医院门诊部功能布局优化设计研究.pdf",
            "chunk_id": "paper-1",
            "snippet": "案例研究说明。",
            "pdf_url": "/documents/pdf?path=paper.pdf",
        },
    ]
    candidates = initial + [
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "chunk_id": "code-1",
            "snippet": "综合医院基地选址应符合规划、交通和环境要求。",
            "retrieval_lane": "standards_first",
            "evidence_tier": "code_spec",
        }
    ]

    selected = synthesizer_agent._ensure_required_lane_citations(
        initial,
        candidates,
        evidence_plan,
        max_citations=2,
    )

    assert len(selected) == 2
    tiers = synthesizer_agent._build_evidence_tiers(selected)
    assert tiers["code_spec"][0]["source"] == "GB 51039-2014 综合医院建筑设计规范.pdf"
