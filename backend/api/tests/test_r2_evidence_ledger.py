import asyncio

from backend.app.agents.base_agent import AgentItem, AgentRequest
from backend.app.agents.evidence_orchestration import (
    EvidenceCard,
    audit_claim_support,
    audit_evidence_coverage,
    build_authority_evidence_need,
    build_evidence_ledger,
    build_evidence_plan_for_query,
    build_evidence_tiers,
    classify_claim_required_scope,
    infer_claim_scopes,
    _split_answer_claims,
)
from backend.app.agents.result_synthesizer_agent import agent as synthesizer_agent


def test_build_ledger_assigns_authority_and_card_ids():
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "clause 3.2.2",
            "snippet": "nurse station service radius should not exceed 30m",
            "chunk_id": "gb-c1",
        },
        {
            "source": "hospital design guide.pdf",
            "location": "nursing unit",
            "snippet": "nursing unit layout should balance observation efficiency and patient privacy",
            "chunk_id": "guide-c1",
        },
    ]

    ledger = build_evidence_ledger(citations)

    assert [card.card_id for card in ledger.cards] == ["E1", "E2"]
    assert isinstance(ledger.cards[0], EvidenceCard)
    assert ledger.cards[0].source_role == "code_spec"
    assert ledger.cards[0].authority_level == 100
    assert ledger.cards[1].source_role == "guide"
    assert ledger.cards[1].authority_level == 70


def test_ledger_filters_inference_context_from_citable_cards():
    ledger = build_evidence_ledger(
        [
            {"source": "multiple", "location": "knowledge graph node: Community", "snippet": "[multiple] Space community"},
            {"source": "[unknown]", "snippet": "unknown"},
        ]
    )

    assert ledger.cards == []
    assert ledger.rejected_count == 2


def test_code_spec_clause_supports_normative_and_numeric_scopes():
    scopes = infer_claim_scopes(
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "clause",
            "snippet": "nurse station service radius should not exceed 30m",
        }
    )

    assert "normative_requirement" in scopes
    assert "numeric_parameter" in scopes


def test_dimension_table_does_not_support_quantity_configuration():
    scopes = infer_claim_scopes(
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 5.7.4",
            "snippet": "operating room clear plane dimension 6.0m x 6.0m and area requirement",
        }
    )

    assert "numeric_parameter" in scopes
    assert "quantity_configuration" not in scopes


def test_count_table_supports_quantity_configuration():
    scopes = infer_claim_scopes(
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 3.2.2",
            "snippet": "bed count, outpatient volume, emergency volume and operation volume may be used as scale configuration basis",
        }
    )

    assert "quantity_configuration" in scopes


def test_atlas_supports_spatial_layout_and_image_reference_not_normative():
    scopes = infer_claim_scopes(
        {
            "source": "medical function room atlas.pdf",
            "snippet": "plan layout diagram, clean dirty flow, room configuration reference",
        }
    )

    assert "spatial_layout" in scopes
    assert "image_reference" in scopes
    assert "normative_requirement" not in scopes


def test_tiers_include_ledger_claim_scopes_and_card_ids():
    tiers = build_evidence_tiers(
        [
            {
                "source": "GB 51039-2014 hospital design code.pdf",
                "location": "table 5.7.4",
                "snippet": "operating room clear plane dimension 6.0m x 6.0m",
                "chunk_id": "c1",
            }
        ]
    )

    card = tiers["code_spec"][0]
    assert card["card_id"] == "E1"
    assert "numeric_parameter" in card["claim_scopes"]
    assert "quantity_configuration" not in card["claim_scopes"]


def test_claim_classifier_detects_quantity_configuration_claim():
    scope = classify_claim_required_scope("Operating room quantity should be configured by bed count and operation volume.")
    assert scope == "quantity_configuration"


def test_claim_classifier_detects_normative_claim():
    scope = classify_claim_required_scope("The nurse station service radius should not exceed 30m.")
    assert scope == "normative_requirement"


def test_claim_audit_rejects_dimension_table_for_quantity_claim():
    final_answer = "Operating room quantity should be determined according to table 5.7.4. [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 5.7.4",
            "snippet": "operating room clear plane dimension 6.0m x 6.0m",
            "chunk_id": "c1",
        }
    ]

    audit = audit_claim_support(final_answer, citations)

    assert audit.passed is False
    assert audit.bindings[0].required_scope == "quantity_configuration"
    assert "does not support required scope" in audit.bindings[0].reason


def test_claim_audit_accepts_direct_numeric_normative_claim():
    final_answer = "The nurse station service radius should not exceed 30m. [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "clause",
            "snippet": "nurse station service radius should not exceed 30m",
            "chunk_id": "c1",
        }
    ]

    audit = audit_claim_support(final_answer, citations)

    assert audit.passed is True
    assert audit.unsupported_claim_count == 0


def test_claim_audit_accepts_policy_principle_claim_from_policy_document():
    final_answer = "政策文件明确医疗机构设置应坚持需求导向原则。 [1]"
    citations = [
        {
            "source": "医疗机构设置规划指导原则（2021-2025年）.pdf",
            "location": "二、医疗机构设置的基本原则",
            "snippet": "医疗机构设置应当坚持需求导向原则，统筹人口、医疗服务需求和资源配置。",
            "chunk_id": "policy-principles",
            "doc_category": "政策文件",
        }
    ]

    audit = audit_claim_support(final_answer, citations)

    assert audit.passed is True
    assert audit.unsupported_claim_count == 0


def test_synthesizer_diagnostics_include_evidence_ledger(monkeypatch):
    async def fake_retry(*args, **kwargs):
        class Response:
            content = "The nurse station service radius should not exceed 30m. [1]"

        return Response()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", fake_retry)

    query = "What is the nurse station service radius requirement?"
    state = {
        "query": query,
        "request": AgentRequest(query=query, metadata={"retrieval_mode": "R2"}),
        "aggregated_items": [
            AgentItem(
                name="GB clause",
                source="mongodb",
                snippet="nurse station service radius should not exceed 30m",
                citations=[
                    {
                        "source": "GB 51039-2014 hospital design code.pdf",
                        "location": "clause",
                        "snippet": "nurse station service radius should not exceed 30m",
                        "chunk_id": "gb-c1",
                    }
                ],
            )
        ],
        "worker_responses": [],
        "notes": [],
    }

    result = asyncio.run(synthesizer_agent.node_synthesize(state))

    diagnostics = result["synthesizer_diagnostics"]
    assert "evidence_ledger" in diagnostics
    assert diagnostics["evidence_ledger"]["cards"][0]["card_id"] == "E1"
    assert diagnostics["claim_support_audit"]["passed"] is True


def test_synthesizer_claim_audit_gates_dimension_overextension(monkeypatch):
    async def fake_retry(*args, **kwargs):
        class Response:
            content = "Operating room quantity should be determined according to table 5.7.4. [1]"

        return Response()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", fake_retry)

    query = "How should operating room quantity be configured?"
    state = {
        "query": query,
        "request": AgentRequest(query=query, metadata={"retrieval_mode": "R2"}),
        "aggregated_items": [
            AgentItem(
                name="operating room dimension table",
                source="mongodb",
                snippet="operating room clear plane dimension 6.0m x 6.0m",
                citations=[
                    {
                        "source": "GB 51039-2014 hospital design code.pdf",
                        "location": "table 5.7.4",
                        "snippet": "operating room clear plane dimension 6.0m x 6.0m",
                        "chunk_id": "gb-dim",
                    }
                ],
            )
        ],
        "worker_responses": [],
        "notes": [],
    }

    result = asyncio.run(synthesizer_agent.node_synthesize(state))

    diagnostics = result["synthesizer_diagnostics"]
    pre_gate_audit = diagnostics["pre_gate_claim_support_audit"]

    # The audit still DETECTS the unsupported over-extension (dimension table
    # cannot support a quantity claim) and records it in diagnostics.
    assert pre_gate_audit["passed"] is False
    assert pre_gate_audit["unsupported_claim_count"] >= 1
    # But grounding is non-destructive: with usable evidence present the model's
    # prose is kept verbatim rather than pruned.
    assert diagnostics["claim_support_gate_applied"] is False
    assert "quantity should be determined according to table 5.7.4" in result["final_answer"]


def test_synthesizer_claim_gate_does_not_modify_vrag_baseline(monkeypatch):
    unsupported_answer = "Operating room quantity should be determined according to table 5.7.4. [1]"

    async def fake_retry(*args, **kwargs):
        class Response:
            content = unsupported_answer

        return Response()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", fake_retry)

    query = "How should operating room quantity be configured?"
    state = {
        "query": query,
        "request": AgentRequest(query=query, metadata={"retrieval_mode": "VRAG"}),
        "aggregated_items": [
            AgentItem(
                name="operating room dimension table",
                source="mongodb",
                snippet="operating room clear plane dimension 6.0m x 6.0m",
                citations=[
                    {
                        "source": "GB 51039-2014 hospital design code.pdf",
                        "location": "table 5.7.4",
                        "snippet": "operating room clear plane dimension 6.0m x 6.0m",
                        "chunk_id": "gb-dim",
                    }
                ],
            )
        ],
        "worker_responses": [],
        "notes": [],
    }

    result = asyncio.run(synthesizer_agent.node_synthesize(state))

    diagnostics = result["synthesizer_diagnostics"]
    assert diagnostics["pre_gate_claim_support_audit"]["passed"] is False
    assert diagnostics["claim_support_gate_applied"] is False
    assert result["final_answer"] == unsupported_answer


def test_claim_support_gate_keeps_supported_policy_facts_instead_of_empty_refusal():
    generated_answer = "医疗机构设置应坚持需求导向原则，并按人口和医疗需求配置。 [1]\n\n另应优先采用未证实的空间重构策略。 [2]"
    citations = [
        {
            "source": "医疗机构设置规划指导原则（2021-2025年）.pdf",
            "location": "二、医疗机构设置的基本原则",
            "snippet": "医疗机构设置应当坚持需求导向原则，统筹人口、医疗服务需求和资源配置。",
            "chunk_id": "policy-principles",
            "doc_category": "政策文件",
        },
        {
            "source": "医疗功能房间详图集3.pdf",
            "location": "急诊流线图",
            "snippet": "急诊空间可参考平面图组织。",
            "chunk_id": "atlas-flow",
            "doc_category": "图集",
        },
    ]
    evidence_tiers = build_evidence_tiers(citations)
    coverage_audit = {
        "passed": True,
        "missing_required_lanes": [],
        "weak_lanes": [],
        "notes": [],
    }
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="《医疗机构设置规划指导原则（2021-2025年）》明确了医疗机构设置应坚持哪些基本原则？",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=evidence_tiers,
        coverage_audit=coverage_audit,
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    assert claim_audit.passed is False
    # Non-destructive grounding: with usable evidence the full answer is kept
    # verbatim; the unsupported claim is measured in diagnostics, not deleted.
    assert diagnostics["claim_support_gate_applied"] is False
    assert "需求导向原则" in final_answer
    assert "人口" in final_answer
    assert final_answer == generated_answer
    assert diagnostics["pre_gate_claim_support_audit"]["unsupported_claim_count"] >= 1


def test_claim_support_gate_prefers_supported_original_claims_over_evidence_list():
    generated_answer = "政策文件明确医疗机构设置应坚持需求导向原则。 [1]\n\n另应优先采用未证实的空间重构策略。 [2]"
    citations = [
        {
            "source": "医疗机构设置规划指导原则（2021-2025年）.pdf",
            "location": "二、医疗机构设置的基本原则",
            "snippet": "医疗机构设置应当坚持需求导向原则，统筹人口、医疗服务需求和资源配置。",
            "chunk_id": "policy-principles",
            "doc_category": "政策文件",
        },
        {
            "source": "医疗功能房间详图集3.pdf",
            "location": "急诊流线图",
            "snippet": "急诊空间可参考平面图组织。",
            "chunk_id": "atlas-flow",
            "doc_category": "图集",
        },
    ]
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="《医疗机构设置规划指导原则（2021-2025年）》明确了医疗机构设置应坚持哪些基本原则？",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=build_evidence_tiers(citations),
        coverage_audit={"passed": True, "missing_required_lanes": [], "weak_lanes": [], "notes": []},
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    assert claim_audit.passed is False
    # Non-destructive: the supported original claim is preserved in place and
    # the whole answer is kept verbatim (the unsupported [2] claim is measured,
    # not deleted).
    assert diagnostics["claim_support_gate_applied"] is False
    assert "政策文件明确医疗机构设置应坚持需求导向原则。 [1]" in final_answer
    assert final_answer == generated_answer
    assert "### 有限证据结论" not in final_answer


def test_q005_dimension_table_cannot_support_or_quantity_configuration():
    answer = "Operating room quantity configuration may be determined from the operating room dimension table. [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 5.7.4",
            "snippet": "operating room clear plane dimensions include length, width and area requirements",
            "chunk_id": "q005-dim-table",
        }
    ]

    audit = audit_claim_support(answer, citations)

    assert audit.passed is False
    assert audit.bindings[0].required_scope == "quantity_configuration"


def test_quantity_configuration_requires_direct_scale_or_count_evidence():
    answer = "Operating room quantity configuration should combine bed count, outpatient volume, emergency volume and operation volume. [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 3.2.2",
            "snippet": "bed count, outpatient volume, emergency volume, operation volume and medical equipment count may be used as medical function scale configuration basis",
            "chunk_id": "scale-table",
        }
    ]

    audit = audit_claim_support(answer, citations)

    assert audit.passed is True


def test_r2_keeps_full_answer_but_measures_unsupported_when_evidence_exists():
    generated_answer = "Operating room quantity should be determined according to table 5.7.4. [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 5.7.4",
            "snippet": "operating room clear plane dimension 6.0m x 6.0m",
            "chunk_id": "gb-dim",
        }
    ]
    evidence_tiers = build_evidence_tiers(citations)
    coverage_audit = {
        "passed": True,
        "missing_required_lanes": [],
        "weak_lanes": [],
        "notes": [],
    }
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="How should operating room quantity be configured?",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=evidence_tiers,
        coverage_audit=coverage_audit,
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    assert claim_audit.passed is False
    # Usable evidence exists -> answer kept verbatim, audit recorded only.
    assert final_answer == generated_answer
    assert diagnostics["claim_support_gate_applied"] is False
    assert diagnostics["original_unsupported_claim_count"] >= 1


def test_claim_support_gate_preserves_required_lane_citations():
    generated_answer = "The nurse station must use this unsupported 42m layout rule. [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "clause 3.2.2",
            "snippet": "nurse station service radius should not exceed 30m",
            "chunk_id": "gb-required",
        }
    ]
    evidence_tiers = build_evidence_tiers(citations)
    claim_audit = audit_claim_support(generated_answer, citations)

    _, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="What is the nurse station service radius requirement?",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=evidence_tiers,
        coverage_audit={"passed": True, "missing_required_lanes": [], "weak_lanes": [], "notes": []},
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    preserved = diagnostics["final_citations"]
    assert preserved[0]["chunk_id"] == "gb-required"
    assert preserved[0]["source"].startswith("GB 51039")


# ---------------------------------------------------------------------------
# Step 1: atomic claim splitter (Chinese sentence boundaries + uncited claims)
# ---------------------------------------------------------------------------


def test_split_answer_claims_splits_chinese_sentences_without_whitespace():
    # Two Chinese sentences joined with no whitespace after the period.
    answer = "手术室应维持正压[1]。换气次数必须达到规定要求[1]。"

    claims = _split_answer_claims(answer)

    assert len(claims) == 2
    assert claims[0].startswith("手术室应维持正压")
    assert claims[1].startswith("换气次数必须达到规定要求")


def test_split_answer_claims_keeps_sentences_without_citation():
    # First sentence is cited, second is an uncited design inference.
    answer = "手术室应维持正压[1]。因此应保证换气次数达标。"

    claims = _split_answer_claims(answer)

    assert len(claims) == 2
    assert any("因此应保证换气次数达标" in claim for claim in claims)


def test_claim_audit_marks_uncited_inference_as_unsupported():
    # The cited fact is supported. The trailing inference continues the same
    # line, so it INHERITS [1] (citation inheritance), but the inherited card is
    # a normative code clause that does not support a quantity-configuration
    # claim, so the inference is still correctly unsupported (off-scope).
    answer = "护士站服务半径不应超过30m[1]。因此护士站数量应按床位数配置。"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "clause 3.2.2",
            "snippet": "nurse station service radius should not exceed 30m",
            "chunk_id": "gb-c1",
        }
    ]

    audit = audit_claim_support(answer, citations)

    assert audit.passed is False
    assert audit.unsupported_claim_count >= 1
    # The inference is unsupported because of scope mismatch, not absent citation.
    inference = [b for b in audit.bindings if "数量应按床位数配置" in b.claim]
    assert inference
    assert inference[0].supported is False
    assert inference[0].reason == "citation does not support required scope"


def test_split_answer_claims_ignores_markdown_headers_and_structure():
    # Headers / bullet labels are structural, not substantive claims; they must
    # not be counted as uncited claims (that would inflate unsupported counts).
    answer = (
        "### 简要总结\n"
        "手术室应维持正压[1]。\n"
        "### 详细说明\n"
        "因此应保证换气次数达标。"
    )

    claims = _split_answer_claims(answer)

    assert not any(claim.startswith("###") for claim in claims)
    assert any("手术室应维持正压" in claim for claim in claims)
    assert any("因此应保证换气次数达标" in claim for claim in claims)


def test_split_answer_claims_excludes_boundary_section_disclaimers():
    # Sentences under a 回答边界 / 推论边界 heading are disclaimers about what
    # was excluded; they are not auditable substantive claims and must not be
    # split out as (uncited, unsupported) claims.
    answer = (
        "### 保留结论\n"
        "护士站服务半径不应超过30m[1]。\n"
        "### 回答边界\n"
        "已删除原回答中引用支撑不足的主张；未在保留结论中出现的数值不作为本次结论。"
    )

    claims = _split_answer_claims(answer)

    assert any("护士站服务半径不应超过30m" in claim for claim in claims)
    assert not any("已删除原回答中引用支撑不足" in claim for claim in claims)
    assert not any("未在保留结论中出现的数值" in claim for claim in claims)


def test_split_answer_claims_excludes_colon_terminated_lead_in_labels():
    # A colon-terminated lead-in introduces a list; it is a label, not an
    # auditable assertion, and must not surface as an uncited claim.
    answer = (
        "以下仅保留当前证据可直接支持的内容：\n"
        "护士站服务半径不应超过30m[1]。"
    )

    claims = _split_answer_claims(answer)

    assert any("护士站服务半径不应超过30m" in claim for claim in claims)
    assert not any(claim.rstrip().endswith(("：", ":")) for claim in claims)


# ---------------------------------------------------------------------------
# Step 2: gate as a per-claim editor (keep supported claims even when coverage
# fails; only fully fall back when zero supported claims remain)
# ---------------------------------------------------------------------------


def test_gate_keeps_supported_claims_when_coverage_not_passed():
    # Coverage audit FAILS (missing required code_spec lane), but one claim is
    # supported by policy evidence. The old gate discarded everything and went
    # to full fallback. The editor must keep the supported claim.
    generated_answer = (
        "医疗机构设置应坚持需求导向原则。 [1]\n\n"
        "因此手术室净宽必须不小于3米。"
    )
    citations = [
        {
            "source": "医疗机构设置规划指导原则（2021-2025年）.pdf",
            "location": "二、医疗机构设置的基本原则",
            "snippet": "医疗机构设置应当坚持需求导向原则，统筹人口、医疗服务需求和资源配置。",
            "chunk_id": "policy-principles",
            "doc_category": "政策文件",
        }
    ]
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="医疗机构设置应坚持哪些基本原则？",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=build_evidence_tiers(citations),
        coverage_audit={
            "passed": False,
            "missing_required_lanes": ["code_spec"],
            "weak_lanes": [],
            "notes": ["missing required evidence lane: code_spec"],
        },
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    assert claim_audit.passed is False
    # Coverage failing flags incompleteness, but usable evidence exists, so the
    # answer is kept verbatim (non-destructive); the unsupported design
    # inference is measured in diagnostics rather than deleted.
    assert diagnostics["claim_support_gate_applied"] is False
    assert "需求导向原则" in final_answer
    assert final_answer == generated_answer
    assert diagnostics["pre_gate_claim_support_audit"]["unsupported_claim_count"] >= 1


def test_gate_keeps_answer_when_unsupported_but_evidence_present():
    # An unsupported claim that nonetheless has a usable evidence card present
    # is kept verbatim (non-destructive). Fallback is reserved for true
    # zero-evidence, covered by test_gate_falls_back_only_on_true_zero_evidence.
    generated_answer = "手术室数量应按表5.7.4确定。 [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 5.7.4",
            "snippet": "operating room clear plane dimension 6.0m x 6.0m",
            "chunk_id": "gb-dim",
        }
    ]
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="手术室数量应如何配置？",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=build_evidence_tiers(citations),
        coverage_audit={"passed": True, "missing_required_lanes": [], "weak_lanes": [], "notes": []},
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    assert claim_audit.passed is False
    # Usable evidence exists -> kept verbatim, measured only.
    assert diagnostics["claim_support_gate_applied"] is False
    assert final_answer == generated_answer
    assert diagnostics["pre_gate_claim_support_audit"]["unsupported_claim_count"] >= 1


def test_claim_gate_never_replaces_with_structured_template_when_retrieved_tiers_exist():
    generated_answer = "门诊部应结合候诊、诊室和公共空间组织流线，并兼顾患者到达与医护工作效率。"
    citations = []
    evidence_tiers = build_evidence_tiers(
        [
            {
                "source": "医院建筑设计指南.pdf",
                "location": "门诊部",
                "snippet": "门诊部功能布局应结合候诊、诊室、公共空间和人流组织综合考虑。",
                "chunk_id": "guide-outpatient",
                "doc_category": "书籍报告",
            }
        ]
    )
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="门诊部如何组织候诊、诊室和公共空间？",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=evidence_tiers,
        coverage_audit={"passed": True, "missing_required_lanes": [], "weak_lanes": [], "notes": []},
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    assert final_answer == generated_answer
    assert diagnostics["claim_support_gate_applied"] is False
    assert "空间约束" not in final_answer
    assert "推论边界" not in final_answer


# ---------------------------------------------------------------------------
# Step 4: scope sufficiency (role + scope, not mere scope existence)
# ---------------------------------------------------------------------------


def test_atlas_card_cannot_support_quantity_configuration_claim():
    # An atlas/image whose caption happens to mention counts must NOT support a
    # quantity_configuration claim; quantity needs an authoritative source.
    answer = "手术室数量配置应按手术量与床位数确定。 [1]"
    citations = [
        {
            "source": "医疗功能房间详图集3.pdf",
            "location": "手术部房间配置图",
            "snippet": "房间配置示意：手术室、麻醉准备间数量及床位数布置参考。",
            "chunk_id": "atlas-count",
            "doc_category": "图集",
        }
    ]

    audit = audit_claim_support(answer, citations)

    assert audit.passed is False
    assert audit.bindings[0].required_scope == "quantity_configuration"


def test_count_table_from_code_spec_still_supports_quantity_configuration():
    # Regression guard: an authoritative count/scale table still supports it.
    answer = "手术室数量配置应结合床位数、门诊量与手术量确定。 [1]"
    citations = [
        {
            "source": "GB 51039-2014 hospital design code.pdf",
            "location": "table 3.2.2",
            "snippet": "bed count, outpatient volume, emergency volume and operation volume may be used as scale configuration basis",
            "chunk_id": "scale-table",
        }
    ]

    audit = audit_claim_support(answer, citations)

    assert audit.passed is True


def test_paper_card_cannot_support_spatial_layout_claim():
    # A paper/report supports empirical inference, not spatial-layout facts.
    answer = "手术室与无菌物品库相邻，平面组织紧凑。 [1]"
    citations = [
        {
            "source": "某医院流程优化研究报告.pdf",
            "location": "第3章 案例分析",
            "snippet": "研究表明优化布局有助于提升手术周转效率。",
            "chunk_id": "paper-study",
            "doc_category": "论文",
        }
    ]

    audit = audit_claim_support(answer, citations)

    assert audit.passed is False
    assert audit.bindings[0].required_scope == "spatial_layout"


# ---------------------------------------------------------------------------
# Step 5: coverage audit checks evidence-point hit, not just lane existence
# ---------------------------------------------------------------------------


def test_coverage_audit_marks_off_topic_required_lane_as_weak():
    # A surgery + ventilation question whose only code_spec card is an unrelated
    # table-of-contents page: the lane EXISTS but hits none of the question's
    # domain/constraint terms, so coverage must not pass.
    query = "洁净手术部的通风空调系统应如何满足分区与气流组织要求？"
    profile, _context, plan = build_evidence_plan_for_query(query, {"source_type": "technical_standard"})
    need = build_authority_evidence_need(query, profile, plan)
    tiers = build_evidence_tiers(
        [
            {
                "source": "GB 51039-2014 hospital design code.pdf",
                "location": "目录",
                "snippet": "目录 第1章 总则 第2章 术语 第3章 基本规定",
                "chunk_id": "toc-1",
            }
        ]
    )

    audit = audit_evidence_coverage(plan, tiers, need=need)

    assert "code_spec" in plan.required_lanes
    assert audit.passed is False
    assert "code_spec" in audit.weak_lanes


def test_coverage_audit_passes_when_required_lane_hits_question_terms():
    query = "洁净手术部的通风空调系统应如何满足分区与气流组织要求？"
    profile, _context, plan = build_evidence_plan_for_query(query, {"source_type": "technical_standard"})
    need = build_authority_evidence_need(query, profile, plan)
    tiers = build_evidence_tiers(
        [
            {
                "source": "GB 51039-2014 hospital design code.pdf",
                "location": "第7章 洁净手术部",
                "snippet": "洁净手术部应按洁污分区组织气流，手术室换气次数与压力梯度应符合规定。",
                "chunk_id": "clean-or-7",
            }
        ]
    )

    audit = audit_evidence_coverage(plan, tiers, need=need)

    # The code_spec card hits the question's domain/constraint terms, so it is
    # neither missing nor weak (other required lanes may still be missing).
    assert "code_spec" not in audit.weak_lanes
    assert "code_spec" not in audit.missing_required_lanes


def test_coverage_audit_without_need_is_backward_compatible():
    # Existing callers that pass no need must keep the lane-existence behaviour.
    query = "护士站服务半径要求"
    profile, _context, plan = build_evidence_plan_for_query(query, {"source_type": "technical_standard"})
    tiers = build_evidence_tiers(
        [
            {
                "source": "GB 51039-2014 hospital design code.pdf",
                "location": "目录",
                "snippet": "目录 第1章 总则",
                "chunk_id": "toc-2",
            }
        ]
    )

    audit = audit_evidence_coverage(plan, tiers)

    # Lane exists with a usable snippet; without need, it passes as before.
    assert audit.passed is True


# ---------------------------------------------------------------------------
# 2026-06-14: Non-destructive grounding. The claim-support audit must MEASURE
# unsupported claims (into diagnostics) without DELETING the model's prose.
# Deleting 80% of sentences produced fragmented, lower-scoring answers; the
# gate is now a measurement layer, not a destructive rewrite. Only true
# zero-evidence (no usable citation cards) falls back to a structured answer.
# ---------------------------------------------------------------------------


def test_gate_is_non_destructive_when_evidence_exists():
    # A long, coherent answer with a mix of supported and unsupported claims.
    # Evidence cards exist. The gate must KEEP the generated answer verbatim
    # and record the audit in diagnostics instead of pruning sentences.
    generated_answer = (
        "住院护理单元应以患者为中心组织。 [1]\n\n"
        "每个护理单元的总床位通常为30~40张，每位护士负责4~8张床位。 [1]\n\n"
        "护士站应能直接观察病房，平衡安全与隐私。"
    )
    citations = [
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "location": "5.5.5",
            "snippet": "护理单元床位数与护士站设置应满足观察与通视要求。",
            "chunk_id": "gb-5-5-5",
        }
    ]
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="住院护理单元应如何组织？",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=build_evidence_tiers(citations),
        coverage_audit={"passed": True, "missing_required_lanes": [], "weak_lanes": [], "notes": []},
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    # The model's prose is preserved verbatim (non-destructive).
    assert final_answer == generated_answer
    # No destructive boilerplate is appended.
    assert "已删除原回答" not in final_answer
    assert "仅保留当前证据可直接支持的内容" not in final_answer
    # The audit is still recorded for the unsupported_claim metric.
    assert "pre_gate_claim_support_audit" in diagnostics
    assert diagnostics["claim_support_gate_applied"] is False


def test_gate_falls_back_only_on_true_zero_evidence():
    # Even with no usable evidence cards, the claim audit is diagnostic only:
    # it must not replace the answer with a fixed refusal template.
    generated_answer = "手术室数量应按表5.7.4确定。 [1]"
    citations = []  # zero evidence
    claim_audit = audit_claim_support(generated_answer, citations)

    final_answer, diagnostics = synthesizer_agent._select_final_answer_after_claim_audit(
        query="手术室数量应如何配置？",
        generated_answer=generated_answer,
        final_citations=citations,
        evidence_tiers=build_evidence_tiers(citations),
        coverage_audit={"passed": False, "missing_required_lanes": ["code_spec"], "weak_lanes": [], "notes": []},
        claim_support_audit=claim_audit,
        benchmark_or_qa_mode=True,
    )

    assert diagnostics["claim_support_gate_applied"] is False
    assert diagnostics["claim_support_gate_reason"] == "claim_support_measured_only"
    assert final_answer == generated_answer


def test_multisource_design_expansion_is_skipped_when_coverage_not_passed():
    citations = [
        {
            "source": "医院建筑设计指南.pdf",
            "snippet": "护理单元布局应兼顾观察效率和患者隐私。",
            "chunk_id": "guide-1",
        },
        {
            "source": "医疗功能房间详图集3.pdf",
            "snippet": "护理单元平面图展示护士站、病房和走廊关系。",
            "chunk_id": "atlas-1",
        },
    ]
    text = "护士站应结合护理路径和患者隐私组织。 [1]"

    expanded = synthesizer_agent._append_multisource_design_expansion(
        query="如何在满足30米服务半径时平衡护士站通视和患者隐私？",
        text=text,
        citations=citations,
        evidence_tiers=build_evidence_tiers(citations),
        coverage_passed=False,
    )

    assert expanded == text


# ---------------------------------------------------------------------------
# 2026-06-14: Citation inheritance. Humans cite once per point: the lead
# sentence of a list item / paragraph carries [n], and follow-up sentences in
# the SAME block continue that point without repeating the marker. The audit
# must treat such a follow-up as inheriting the block's citation, otherwise the
# unsupported_claim metric is inflated by normal prose structure.
# ---------------------------------------------------------------------------


def test_continuation_sentence_inherits_block_citation():
    # Same line/block: the lead sentence cites [1] (a code_spec normative card);
    # the next sentence continues the same point on the same line with no
    # marker. The continuation must inherit [1] so a normal "cite once per
    # point" structure is not counted as an unsupported claim. (Real answers
    # keep continuations on the same line; newlines separate list items.)
    answer = "护理单元应满足护士站对病房的观察与通视要求 [1]。因此应据此控制相应的设置要求。"
    citations = [
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "location": "5.5.5",
            "snippet": "护理单元应满足护士站对病房的观察与通视要求，应规定相应距离。",
            "chunk_id": "gb-5-5-5",
        }
    ]

    audit = audit_claim_support(answer, citations)

    # Both the lead and the continuation are supported; none counts as unsupported.
    assert audit.unsupported_claim_count == 0
    assert audit.passed is True


def test_new_block_does_not_inherit_previous_block_citation():
    # A fresh list item with its own uncited claim must NOT inherit the prior
    # item's citation; inheritance is block-local, not document-global.
    answer = (
        "1. 护理单元应满足观察与通视要求 [1]。\n"
        "2. 另外应优先采用未证实的空间重构策略。"
    )
    citations = [
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "location": "5.5.5",
            "snippet": "护理单元应满足护士站对病房的观察与通视要求。",
            "chunk_id": "gb-5-5-5",
        }
    ]

    audit = audit_claim_support(answer, citations)

    # The second item is a separate block with no citation -> still unsupported.
    assert audit.unsupported_claim_count >= 1
