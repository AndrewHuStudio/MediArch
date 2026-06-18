from backend.app.agents.evidence_orchestration import (
    audit_evidence_coverage,
    build_authority_evidence_need,
    build_authority_records,
    build_evidence_plan,
    build_evidence_plan_for_query,
    build_evidence_tiers,
    build_source_aware_evidence_plan,
    build_standards_first_queries,
    build_supplemental_lane_queries,
    classify_source_role,
    EvidenceContext,
    rank_authority_records,
    profile_question,
)
from backend.app.agents.result_synthesizer_agent.agent import build_structured_fallback_answer
from backend.app.agents.result_synthesizer_agent import agent as synthesizer_agent
from backend.app.agents.base_agent import AgentRequest
import asyncio


def test_profile_normative_spatial_design_question_requires_multiple_lanes():
    profile = profile_question(
        "在现代住院部设计中，如何通过空间排布在满足30米服务半径的同时，解决护士站通视性与患者隐私保护之间的矛盾？"
    )

    assert profile.requires_code_spec is True
    assert profile.requires_design_translation is True
    assert profile.requires_spatial_reference is True
    assert "numeric_boundary" in profile.constraint_types
    assert "visibility" in profile.constraint_types
    assert "privacy" in profile.constraint_types
    assert "inpatient_nursing_unit" in profile.medical_domains


def test_profile_cross_department_code_question_requires_code_spec_and_synthesis():
    profile = profile_question(
        "手术部、放射科、磁共振检查室、放射治疗科和核医学科在选址、分区及流线组织上分别应如何设置？"
    )

    assert profile.requires_code_spec is True
    assert profile.requires_cross_department_synthesis is True
    assert "zoning" in profile.constraint_types
    assert "circulation" in profile.constraint_types


def test_plan_keeps_design_lanes_optional_for_implicit_normative_design():
    profile = profile_question("如何在满足30米服务半径时平衡护士站通视和患者隐私？")
    plan = build_evidence_plan(profile)

    assert "code_spec" in plan.required_lanes
    assert "guide" not in plan.required_lanes
    assert "atlas_or_image" not in plan.required_lanes
    assert "guide" in plan.optional_lanes
    assert "atlas_or_image" in plan.optional_lanes
    assert "paper_or_report" not in plan.required_lanes
    assert plan.minimum_code_spec_evidence >= 1


def test_plan_cross_department_code_question_requires_code_spec_lane():
    profile = profile_question("手术部、放射科、磁共振、放射治疗科和核医学科的选址分区流线如何设置？")
    plan = build_evidence_plan(profile)

    assert "code_spec" in plan.required_lanes
    assert plan.requires_lane_audit is True
    assert plan.minimum_code_spec_evidence >= 1


def test_policy_question_requires_policy_document_not_code_spec():
    query = "《医疗机构设置规划指导原则（2021-2025年）》明确了医疗机构设置应坚持哪些基本原则？"
    _profile, context, plan = build_evidence_plan_for_query(
        query,
        {"source_type": "policy_document", "task_type": "fact", "question_id": "Q019"},
    )

    assert context.source_type == "policy_document"
    assert "policy_document" in plan.required_lanes
    assert "code_spec" not in plan.required_lanes


def test_academic_paper_question_requires_paper_not_code_spec():
    query = "根据《多联手术室布局优化与气流控制》，作者在 CFD 模拟中主要比较了哪三类影响因素？"
    _profile, context, plan = build_evidence_plan_for_query(
        query,
        {"source_type": "academic_paper", "task_type": "fact", "question_id": "Q029"},
    )

    assert context.source_type == "academic_paper"
    assert "paper_or_report" in plan.required_lanes
    assert "code_spec" not in plan.required_lanes


def test_book_report_question_requires_book_or_guide_not_code_spec_by_default():
    query = "根据《医院建筑设计指南》“分区和交通系统”一节，医院建筑通常可分为哪四个功能区？"
    _profile, context, plan = build_evidence_plan_for_query(
        query,
        {"source_type": "book_report", "task_type": "fact", "question_id": "Q045"},
    )

    assert context.source_type == "book_report"
    assert any(lane in plan.required_lanes for lane in ("book_report", "guide"))
    assert "code_spec" not in plan.required_lanes


def test_technical_standard_question_still_requires_code_spec():
    query = "根据《综合医院建筑设计规范》GB 51039-2014，综合医院基地选址与总平面设计应满足哪些核心要求？"
    _profile, context, plan = build_evidence_plan_for_query(
        query,
        {"source_type": "technical_standard", "task_type": "fact", "question_id": "Q006"},
    )

    assert context.source_type == "technical_standard"
    assert "code_spec" in plan.required_lanes


def test_technical_standard_design_lanes_are_optional_unless_explicitly_required():
    query = "根据《综合医院建筑设计规范》GB 51039-2014，护士站通视和患者隐私应如何平衡？"
    _profile, context, plan = build_evidence_plan_for_query(
        query,
        {"source_type": "technical_standard", "task_type": "spatial_reasoning", "question_id": "Q999"},
    )

    assert context.source_type == "technical_standard"
    assert plan.required_lanes == ["code_spec"]
    assert "guide" in plan.optional_lanes
    assert "atlas_or_image" in plan.optional_lanes


def test_classify_source_role_from_metadata_and_title():
    assert classify_source_role({"source": "GB 51039-2014 综合医院建筑设计规范"}) == "code_spec"
    assert classify_source_role({"source": "医院建筑设计指南.pdf"}) == "guide"
    assert classify_source_role({"source": "医疗功能房间详图集3.pdf"}) == "atlas_or_image"
    assert classify_source_role({"source": "某医院设计研究论文.pdf", "doc_category": "论文"}) == "paper_or_report"


def test_classify_policy_and_book_sources():
    assert classify_source_role({"source": "医疗机构设置规划指导原则（2021-2025）.pdf"}) == "policy_document"
    assert classify_source_role({"source": "国家医学中心和国家区域医疗中心设置实施方案.pdf"}) == "policy_document"
    assert classify_source_role({"source": "医院建筑设计指南.pdf", "doc_category": "书籍报告"}) == "book_report"


def test_evidence_tiers_include_policy_and_book_report():
    tiers = build_evidence_tiers([
        {"source": "医疗机构设置规划指导原则（2021-2025）.pdf", "snippet": "医疗机构设置应坚持公平可及。"},
        {"source": "医院建筑设计指南.pdf", "doc_category": "书籍报告", "snippet": "医院建筑可分为不同功能区。"},
    ])

    assert tiers["policy_document"][0]["source_role"] == "policy_document"
    assert tiers["book_report"][0]["source_role"] == "book_report"


def test_policy_r2_synthesis_coverage_accepts_policy_document_evidence():
    query = "《医疗机构设置规划指导原则（2021-2025年）》明确了医疗机构设置应坚持哪些基本原则？"
    _profile, context, plan = build_evidence_plan_for_query(
        query,
        {"source_type": "policy_document", "task_type": "fact", "question_id": "Q019"},
    )
    tiers = build_evidence_tiers([
        {
            "source": "医疗机构设置规划指导原则（2021-2025）.pdf",
            "snippet": "医疗机构设置应坚持公平可及、科学布局、协调发展的原则。",
        }
    ])

    audit = audit_evidence_coverage(plan, tiers)

    assert context.source_type == "policy_document"
    assert audit.passed is True
    assert audit.missing_required_lanes == []


def test_kg_unknown_is_inference_context_not_citable_evidence():
    assert classify_source_role({"source": "multiple", "location": "知识图谱节点: Community"}) == "inference_context"


def test_build_evidence_tiers_keeps_roles_separate():
    citations = [
        {"source": "GB 51039-2014 综合医院建筑设计规范", "snippet": "护士站到最远病房门口距离不宜超过30m"},
        {"source": "医院建筑设计指南.pdf", "snippet": "护理单元平面组织"},
        {"source": "multiple", "location": "知识图谱节点: Community", "snippet": "[multiple] Space类型节点社区"},
    ]
    tiers = build_evidence_tiers(citations)

    assert tiers["code_spec"][0]["source_role"] == "code_spec"
    assert tiers["guide"][0]["source_role"] == "guide"
    assert tiers["inference_context"] == []


def test_audit_flags_missing_code_spec_when_required():
    profile = profile_question("如何在满足30米服务半径时平衡护士站通视和患者隐私？")
    plan = build_evidence_plan(profile)
    tiers = build_evidence_tiers([
        {"source": "医院建筑设计指南.pdf", "snippet": "护理单元布局"}
    ])

    audit = audit_evidence_coverage(plan, tiers)

    assert audit.passed is False
    assert "code_spec" in audit.missing_required_lanes
    assert audit.needs_supplemental_retrieval is True


def test_audit_passes_when_required_code_spec_exists_even_without_design_lanes():
    profile = profile_question("如何在满足30米服务半径时平衡护士站通视和患者隐私？")
    plan = build_evidence_plan(profile)
    tiers = build_evidence_tiers([
        {"source": "GB 51039-2014 综合医院建筑设计规范", "snippet": "护士站到最远病房门口距离不宜超过30m"},
    ])

    audit = audit_evidence_coverage(plan, tiers)

    assert audit.passed is True
    assert audit.missing_required_lanes == []


def test_orchestration_payload_for_synthesizer_has_policy_and_tiers():
    query = "如何在满足30米服务半径时平衡护士站通视和患者隐私？"
    citations = [
        {"source": "GB 51039-2014 综合医院建筑设计规范", "snippet": "距离不宜超过30m"},
        {"source": "医院建筑设计指南.pdf", "snippet": "护理单元平面组织"},
        {"source": "医疗功能房间详图集3.pdf", "snippet": "护理单元平面参考"},
    ]
    profile = profile_question(query)
    plan = build_evidence_plan(profile)
    tiers = build_evidence_tiers(citations)
    audit = audit_evidence_coverage(plan, tiers)

    assert plan.answer_policy == "evidence_to_constraints_to_response_to_boundary"
    assert "code_spec" in tiers
    assert audit.passed is True


def test_structured_fallback_does_not_emit_legacy_exploration_template():
    answer = build_structured_fallback_answer(
        query="手术部、放射科、磁共振、放射治疗科和核医学科如何设置？",
        evidence_tiers=build_evidence_tiers([]),
        coverage_audit={
            "passed": False,
            "missing_required_lanes": ["code_spec"],
            "notes": ["缺少规范证据"],
        },
    )

    assert "延伸探索" not in answer
    assert "[unknown]" not in answer
    assert "推论边界" not in answer
    assert "未找到可用资料" in answer


def test_structured_fallback_explains_missing_evidence_lanes_without_internal_key_evidence_label():
    answer = build_structured_fallback_answer(
        query="门诊单元如何设计",
        evidence_tiers=build_evidence_tiers(
            [{"source": "医院建筑设计指南.pdf", "snippet": "门诊单元组织模式。"}]
        ),
        coverage_audit={
            "passed": False,
            "missing_required_lanes": ["code_spec", "atlas_or_image"],
            "weak_lanes": [],
            "notes": [],
        },
    )

    assert "关键证据" not in answer
    assert "资料摘录" in answer
    assert "规范/标准依据" not in answer
    assert "图示/详图依据" not in answer


def test_supplemental_queries_for_missing_code_spec_are_role_based_not_doc_bound():
    query = "如何在满足30米服务半径时平衡护士站通视和患者隐私？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)
    audit = audit_evidence_coverage(plan, build_evidence_tiers([]))

    queries = build_supplemental_lane_queries(query, profile, audit)

    assert "code_spec" in queries
    assert any("规范" in q or "标准" in q for q in queries["code_spec"])
    assert not any("GB 51039" in q for q in queries["code_spec"])


def test_standards_first_queries_for_normative_numeric_question():
    query = "护士站服务半径和通视隐私如何满足规范要求？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)

    queries = build_standards_first_queries(query, profile, plan)

    assert queries
    assert any("规范" in q or "标准" in q or "条文" in q for q in queries)
    assert any("护士站" in q or "服务半径" in q for q in queries)
    assert not any("GB 51039" in q for q in queries)


def test_standards_first_queries_for_cross_department_zoning_question():
    query = "手术部、放射科、磁共振、放疗科和核医学科在选址分区及流线组织上应如何设置？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)

    queries = build_standards_first_queries(query, profile, plan)

    assert queries
    assert any("分区" in q or "流线" in q or "选址" in q for q in queries)
    assert any("规范" in q or "标准" in q or "条文" in q for q in queries)


def test_authority_evidence_need_maps_profile_to_chinese_terms_and_claim_scopes():
    query = "手术部、放射科、磁共振、放疗科和核医学科在选址分区及流线组织上应如何设置？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)

    need = build_authority_evidence_need(query, profile, plan)

    assert need.required_roles == ["code_spec"]
    assert "手术部" in need.domain_terms
    assert "放射科" in need.domain_terms
    assert "磁共振" in need.domain_terms
    assert "放疗" in need.domain_terms
    assert "核医学" in need.domain_terms
    assert "分区" in need.constraint_terms
    assert "流线" in need.constraint_terms
    assert "workflow_zoning" in need.claim_scopes
    assert "spatial_layout" in need.claim_scopes
    assert all("surgery_department" not in term for term in need.search_terms)


def test_authority_need_keeps_optional_spatial_roles_in_search_terms():
    query = "护士站服务半径和通视隐私如何满足规范要求？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)

    need = build_authority_evidence_need(query, profile, plan)

    assert "guide" in need.optional_roles
    assert "atlas_or_image" in need.optional_roles
    assert any("图集" in term or "详图" in term for term in need.search_terms)


def test_standards_first_queries_use_authority_need_terms_not_internal_enums():
    query = "如何在满足30米服务半径时平衡护士站通视和患者隐私？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)

    queries = build_standards_first_queries(query, profile, plan)
    combined = " ".join(queries)

    assert "护理单元" in combined
    assert "病房" in combined
    assert "服务半径" in combined
    assert "通视" in combined
    assert "隐私" in combined
    assert "numeric_boundary" not in combined
    assert "inpatient_nursing_unit" not in combined


def test_build_authority_records_extracts_roles_terms_and_claim_scopes_from_chunks():
    chunks = [
        {
            "chunk_id": "code-1",
            "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "section": "5.5.6",
            "page_range": [42],
            "chunk_text": "护士站到最远病房门口的距离不宜超过30m，并宜通视护理单元。",
        },
        {
            "chunk_id": "atlas-1",
            "doc_title": "医疗功能房间详图集3.pdf",
            "content_type": "image",
            "chunk_text": "护理单元平面布置图，展示护士站、病房与走廊关系。",
            "image_url": "/images/nursing-plan.png",
        },
    ]

    records = build_authority_records(chunks)

    assert len(records) == 2
    code = records[0]
    assert code.source_role == "code_spec"
    assert code.content_type == "clause"
    assert "护理单元" in code.domain_terms
    assert "护士站" in code.domain_terms
    assert "距离" in code.constraint_terms
    assert "通视" in code.constraint_terms
    assert "normative_requirement" in code.claim_scopes
    assert "numeric_parameter" in code.claim_scopes
    assert code.anchor == "5.5.6"
    assert records[1].source_role == "atlas_or_image"
    assert "spatial_layout" in records[1].claim_scopes


def test_rank_authority_records_prefers_code_spec_numeric_match_over_guides():
    query = "护士站服务半径和通视隐私如何满足规范要求？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)
    need = build_authority_evidence_need(query, profile, plan)
    records = build_authority_records(
        [
            {
                "chunk_id": "guide-1",
                "doc_title": "医院建筑设计指南.pdf",
                "chunk_text": "护士站应结合护理路径和患者隐私组织。",
            },
            {
                "chunk_id": "code-1",
                "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
                "section": "5.5.6",
                "chunk_text": "护士站到最远病房门口的距离不宜超过30m。",
            },
        ]
    )

    ranked = rank_authority_records(need, records)

    assert [record.record_id for record in ranked] == ["code-1", "guide-1"]
    assert ranked[0].source_role == "code_spec"
    assert "numeric_parameter" in ranked[0].claim_scopes


def test_rank_authority_records_keeps_atlas_supplement_for_spatial_needs():
    query = "手术部、放射科和核医学科在分区及流线组织上应如何设置？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)
    need = build_authority_evidence_need(query, profile, plan)
    records = build_authority_records(
        [
            {
                "chunk_id": "code-1",
                "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
                "chunk_text": "手术部应自成一区，洁污流线应分明。",
            },
            {
                "chunk_id": "atlas-1",
                "doc_title": "医疗功能房间详图集3.pdf",
                "content_type": "image",
                "chunk_text": "手术部平面图展示洁净区、污染区和医患流线。",
                "image_url": "/images/surgery-flow.png",
            },
            {
                "chunk_id": "paper-1",
                "doc_title": "医院流线组织研究论文.pdf",
                "chunk_text": "案例研究讨论医技科室流线优化。",
            },
        ]
    )

    ranked = rank_authority_records(need, records)

    assert [record.record_id for record in ranked[:2]] == ["code-1", "atlas-1"]
    assert any(record.source_role == "atlas_or_image" for record in ranked)
    assert ranked[-1].source_role == "paper_or_report"


def test_synthesize_empty_r2_state_uses_structured_control_plane_fallback():
    query = "手术部、放射科、磁共振、放射治疗科和核医学科如何设置？"

    result = asyncio.run(
        synthesizer_agent.node_synthesize(
            {
                "query": query,
                "request": AgentRequest(query=query, metadata={"retrieval_mode": "R2"}),
                "aggregated_items": [],
                "worker_responses": [],
                "notes": [],
            }
        )
    )

    assert "延伸探索" not in result["final_answer"]
    assert "未找到可用资料" in result["final_answer"]
    assert result["recommended_questions"] == []
    diagnostics = result["synthesizer_diagnostics"]
    assert diagnostics["fallback_used"] is True
    assert diagnostics["coverage_audit"]["needs_supplemental_retrieval"] is True
    assert "question_profile" in diagnostics
    assert "evidence_plan" in diagnostics


def test_recommended_questions_are_natural_and_hide_internal_graph_labels():
    query = "门诊单元如何组织候诊、诊室和公共空间？"
    graph = {
        "expanded_entities": [
            {"name": "候诊区", "type": "Space"},
            {"name": "模块化分区", "type": "DesignMethod"},
        ],
        "expanded_relations": [
            {"source": "候诊区", "target": "诊室", "relation": "相邻"},
        ],
        "knowledge_coverage": [{"domain": "Space"}, {"domain": "DesignMethod"}],
    }

    questions = synthesizer_agent._build_natural_recommended_questions(
        query=query,
        neo4j_query_path=graph,
        aggregated_items_count=8,
        include_online=False,
    )

    assert questions
    joined = " ".join(questions)
    assert "Space" not in joined
    assert "DesignMethod" not in joined
    assert "最佳实践" not in joined
    assert "候诊区和诊室如何衔接" in joined
    assert all(len(question) <= 34 for question in questions)


def test_non_citable_inference_context_is_removed_from_final_citations():
    citations = [
        {"source": "GB 51039-2014 综合医院建筑设计规范", "snippet": "医疗工艺参数应由工艺设计确定"},
        {"source": "multiple", "location": "知识图谱节点: Community", "snippet": "[multiple] Space节点社区"},
        {"source": "[unknown]", "snippet": "unknown node"},
    ]

    filtered = synthesizer_agent._filter_citable_evidence(citations)

    assert [c["source"] for c in filtered] == ["GB 51039-2014 综合医院建筑设计规范"]


def test_coverage_missing_required_code_spec_still_uses_conservative_synthesis_mode():
    query = "如何在满足30米服务半径时平衡护士站通视和患者隐私？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)
    tiers = build_evidence_tiers([
        {"source": "医院建筑设计指南.pdf", "snippet": "护理单元布局"}
    ])
    audit = audit_evidence_coverage(plan, tiers)

    mode = synthesizer_agent._select_synthesis_mode(plan, audit)

    assert mode["mode"] == "conservative_missing_required_evidence"
    assert mode["allow_full_generation"] is False
    # 保守模式仍比 full 模式更收敛（不再硬编码具体数值，避免与放宽配额冲突）。
    assert mode["max_prompt_documents"] <= 12
    assert "code_spec" in mode["missing_required_lanes"]


def test_coverage_missing_optional_design_lanes_does_not_force_conservative_mode():
    query = "如何在满足30米服务半径时平衡护士站通视和患者隐私？"
    profile = profile_question(query)
    plan = build_evidence_plan(profile)
    tiers = build_evidence_tiers([
        {"source": "GB 51039-2014 综合医院建筑设计规范", "snippet": "护士站到最远病房门口距离不宜超过30m"}
    ])
    audit = audit_evidence_coverage(plan, tiers)

    mode = synthesizer_agent._select_synthesis_mode(plan, audit)

    assert audit.passed is True
    assert mode["mode"] == "full_evidence_grounded"
    assert mode["allow_full_generation"] is True


def test_qa_mode_limits_synthesizer_connection_error_attempts(monkeypatch):
    request = AgentRequest(query="test", metadata={"retrieval_mode": "R2"})

    assert synthesizer_agent._llm_retry_budget_for_request(request, "synthesizer") <= 2
