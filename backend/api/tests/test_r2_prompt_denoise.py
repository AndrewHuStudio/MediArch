"""R2 prompt 去噪/简化回归测试。

需求（docs/实验部分/R2智能体传递简化需求_2026-06-15.md）：让证据干净、完整、低噪地
抵达 LLM。次要引用通道（document/attribute/knowledge_graph_citations，按 agent_name
硬分发 + 100 字截断）与主通道重复且稀释正文，不再进 prompt；编排元数据同样不进 prompt。
本组测试锁定"喂给 LLM 的 prompt context 极简、正文优先、噪声与次要通道被移除"。
"""

from backend.app.agents.result_synthesizer_agent import agent as synthesizer_agent


def _ctx_with_body_evidence():
    return {
        "query": "护士站服务半径要求",
        "total_results": 12,
        "knowledge_graph": {
            "expanded_entities": [{"name": f"E{i}", "type": "X", "score": 0.5} for i in range(8)],
        },
        "knowledge_graph_citations": [{"source": f"kg{i}", "snippet": "三元组"} for i in range(10)],
        "attribute_citations": [{"source": f"attr{i}", "snippet": "属性"} for i in range(10)],
        "document_citations": [{"source": f"doc{i}", "snippet": "文档"} for i in range(10)],
        "evidence_tiers": {
            "code_spec": [{"source": "GB 51039", "snippet": "护士站到最远病房门口距离不宜超过30m。"}],
            "guide": [{"source": "医院建筑设计指南", "snippet": "护理单元组织。"}],
        },
        "citations_catalog": "[1] GB 51039 | 5.5.6 | 护士站到最远病房门口距离不宜超过30m。",
        "documents_view": [{"doc_name": "GB 51039", "role": "code_spec"}],
        "items_summary": [{"title": "x"} for _ in range(6)],
        "key_takeaways": ["a", "b", "c"],
        "question_profile": {"task_type": "fact", "lots": "of metadata"},
        "evidence_context": {"big": "blob"},
        "supplemental_lane_queries": ["q1", "q2"],
    }


def test_denoise_keeps_body_evidence_channels():
    """文档正文证据通道（evidence_tiers/catalog）必须保留。"""
    out = synthesizer_agent._denoise_prompt_context(_ctx_with_body_evidence())

    assert "evidence_tiers" in out
    assert "citations_catalog" in out
    assert out["evidence_tiers"]["code_spec"][0]["source"] == "GB 51039"


def test_denoise_drops_pure_noise_metadata():
    """系统提示从不引用的纯编排元数据应从 prompt 中移除。"""
    out = synthesizer_agent._denoise_prompt_context(_ctx_with_body_evidence())

    for noise_key in ("items_summary", "key_takeaways", "question_profile",
                      "evidence_context", "supplemental_lane_queries", "total_results"):
        assert noise_key not in out, f"{noise_key} 应作为噪声被移除"


def test_secondary_citation_channels_not_in_prompt():
    """次要引用通道（与主通道重复、按 agent_name 硬分发）不再进 prompt。"""
    out = synthesizer_agent._denoise_prompt_context(_ctx_with_body_evidence())

    assert "knowledge_graph_citations" not in out
    assert "attribute_citations" not in out
    assert "document_citations" not in out


def test_enhanced_context_prompt_is_minimal():
    """精简后 prompt context 不应含编排元数据。"""
    noisy = {
        "query": "q",
        "evidence_tiers": {"code_spec": []},
        "citations_catalog": "[1] x",
        "question_profile": {"a": 1},
        "evidence_plan": {"b": 2},
        "coverage_audit": {"c": 3},
        "synthesis_mode": {"d": 4},
        "answer_evidence_policy": {"e": 5},
        "items_summary": [1, 2],
        "documents_view": [1],
        "doc_roles": {"x": 1},
        "unified_hints": {"y": 2},
        "knowledge_graph": {"z": 3},
    }
    out = synthesizer_agent._denoise_prompt_context(noisy)
    for noise_key in (
        "question_profile", "evidence_plan", "coverage_audit",
        "synthesis_mode", "answer_evidence_policy", "items_summary",
        "doc_roles", "unified_hints", "knowledge_graph",
    ):
        assert noise_key not in out, f"{noise_key} 不应进 prompt"
    # documents_view 是宽口径正文通道，多部分问题需要它提供广度，必须保留。
    assert "documents_view" in out, "documents_view 应作为正文广度通道保留"


def test_answer_domain_anchor_keys_on_source_type():
    """不再注入固定领域骨架提示。"""
    policy = synthesizer_agent._format_answer_skeleton("任意", source_type="policy_document")
    paper = synthesizer_agent._format_answer_skeleton("任意", source_type="academic_paper")
    assert policy == ""
    assert paper == ""


def test_system_prompt_anchors_domain_without_persona_or_template():
    """system prompt：保留 Markdown 自由组织与防编造，但无固定章节模板。"""
    src = synthesizer_agent.SYNTHESIZER_SYSTEM_PROMPT
    assert "固定章节模板" in src
    assert "Markdown" in src
    assert "不要虚构" in src
    assert "充分、完整、详尽" in src
    assert "不要简略概括" in src
    assert "不要指定固定章节名" in src
    assert "图文配对" in src
