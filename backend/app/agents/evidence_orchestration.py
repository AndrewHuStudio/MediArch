from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List


EVIDENCE_LANES = (
    "code_spec",
    "policy_document",
    "guide",
    "book_report",
    "atlas_or_image",
    "paper_or_report",
    "inference_context",
)


@dataclass(frozen=True)
class QuestionProfile:
    requires_code_spec: bool = False
    requires_design_translation: bool = False
    requires_spatial_reference: bool = False
    requires_empirical_reasoning: bool = False
    requires_cross_department_synthesis: bool = False
    constraint_types: list[str] = field(default_factory=list)
    medical_domains: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidencePlan:
    required_lanes: list[str] = field(default_factory=list)
    optional_lanes: list[str] = field(default_factory=list)
    minimum_code_spec_evidence: int = 0
    requires_lane_audit: bool = True
    answer_policy: str = "evidence_to_constraints_to_response_to_boundary"


@dataclass(frozen=True)
class EvidenceContext:
    source_type: str = ""
    task_type: str = ""
    difficulty: str = ""
    question_id: str = ""


@dataclass(frozen=True)
class AuthorityEvidenceNeed:
    required_roles: list[str] = field(default_factory=list)
    optional_roles: list[str] = field(default_factory=list)
    domain_terms: list[str] = field(default_factory=list)
    constraint_terms: list[str] = field(default_factory=list)
    claim_scopes: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuthorityEvidenceRecord:
    record_id: str
    source_role: str
    doc_title: str
    text: str
    content_type: str = "text"
    anchor: str | None = None
    page_number: int | str | None = None
    chunk_id: str | None = None
    domain_terms: list[str] = field(default_factory=list)
    constraint_terms: list[str] = field(default_factory=list)
    claim_scopes: list[str] = field(default_factory=list)
    citation: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverageAudit:
    passed: bool
    missing_required_lanes: list[str] = field(default_factory=list)
    weak_lanes: list[str] = field(default_factory=list)
    needs_supplemental_retrieval: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceCard:
    card_id: str
    source_role: str
    authority_level: int
    source: str
    location: str | None = None
    page_number: int | str | None = None
    chunk_id: str | None = None
    snippet: str | None = None
    anchor: str | None = None
    claim_scopes: list[str] = field(default_factory=list)
    support_level: str = "direct"
    citation: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceLedger:
    cards: list[EvidenceCard] = field(default_factory=list)
    rejected_count: int = 0
    rejected_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimBinding:
    claim: str
    required_scope: str
    citation_ids: list[int]
    supported: bool
    reason: str = ""


@dataclass(frozen=True)
class ClaimSupportAudit:
    passed: bool
    bindings: list[ClaimBinding] = field(default_factory=list)
    unsupported_claim_count: int = 0
    notes: list[str] = field(default_factory=list)


AUTHORITY_BY_ROLE = {
    "code_spec": 100,
    "policy_document": 90,
    "guide": 70,
    "book_report": 65,
    "atlas_or_image": 60,
    "paper_or_report": 45,
    "inference_context": 10,
}


DOMAIN_AUTHORITY_TERMS = {
    "inpatient_nursing_unit": ("护理单元", "病房", "住院部", "护士站", "病房门口"),
    "surgery_department": ("手术部", "手术室", "洁净手术部"),
    "emergency_department": ("急诊", "急诊部", "急救"),
    "outpatient_department": ("门诊", "门诊部"),
    "radiology_department": ("放射科", "放射诊断", "医学影像", "磁共振", "MRI"),
    "radiotherapy_department": ("放射治疗", "放疗", "放疗科"),
    "nuclear_medicine": ("核医学", "核医学科"),
    "intensive_care": ("ICU", "重症监护", "重症医学科"),
}


CONSTRAINT_AUTHORITY_TERMS = {
    "numeric_boundary": ("数值要求", "距离", "面积", "净宽", "服务半径"),
    "visibility": ("通视", "视线", "观察"),
    "privacy": ("隐私", "私密", "遮挡"),
    "zoning": ("分区", "洁污分区", "防护分区", "感染控制"),
    "circulation": ("流线", "动线", "医患流线", "洁污流线", "交通组织"),
    "adjacency_or_layout": ("选址", "邻近", "邻接", "布置", "平面组织"),
}


CONSTRAINT_CLAIM_SCOPES = {
    "numeric_boundary": ("numeric_parameter", "normative_requirement"),
    "visibility": ("spatial_layout",),
    "privacy": ("spatial_layout",),
    "zoning": ("workflow_zoning",),
    "circulation": ("workflow_zoning",),
    "adjacency_or_layout": ("spatial_layout",),
}


def _add_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _contains_any(text: str, words: Iterable[str]) -> bool:
    return any(word.lower() in text for word in words)


def _extend_unique(values: list[str], additions: Iterable[str]) -> None:
    for value in additions:
        _add_unique(values, value)


def deduplicate_text_terms(terms: Iterable[str]) -> list[str]:
    values: list[str] = []
    for term in terms or []:
        cleaned = re.sub(r"\s+", " ", str(term or "")).strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values


def profile_question(query: str) -> QuestionProfile:
    text = query or ""
    lower = text.lower()
    constraint_types: list[str] = []
    medical_domains: list[str] = []

    has_numeric = bool(re.search(r"\d+\s*(m|米|mm|毫米|㎡|m2|床|小时|h|%)", lower))
    has_numeric = has_numeric or _contains_any(
        lower,
        ("不小于", "不宜超过", "净宽", "面积", "半径", "距离", "数量", "比例"),
    )
    if has_numeric:
        _add_unique(constraint_types, "numeric_boundary")

    if _contains_any(lower, ("通视", "视线", "观察", "可视")):
        _add_unique(constraint_types, "visibility")
    if _contains_any(lower, ("隐私", "私密")):
        _add_unique(constraint_types, "privacy")
    if _contains_any(lower, ("分区", "洁污", "清污", "防护", "感染控制")):
        _add_unique(constraint_types, "zoning")
    if _contains_any(lower, ("流线", "动线", "医患", "出入口", "交通组织")):
        _add_unique(constraint_types, "circulation")
    if _contains_any(lower, ("邻近", "邻接", "毗邻", "布置", "排布", "组织")):
        _add_unique(constraint_types, "adjacency_or_layout")

    domain_map = (
        ("inpatient_nursing_unit", ("护理单元", "病房", "住院部", "护士站")),
        ("surgery_department", ("手术部", "手术室")),
        ("emergency_department", ("急诊",)),
        ("outpatient_department", ("门诊",)),
        ("radiology_department", ("放射科", "放射", "磁共振", "mri")),
        ("radiotherapy_department", ("放射治疗", "放疗")),
        ("nuclear_medicine", ("核医学",)),
        ("intensive_care", ("icu", "重症")),
    )
    for domain, words in domain_map:
        if _contains_any(lower, words):
            _add_unique(medical_domains, domain)

    normative_signal = _contains_any(
        lower,
        ("规范", "标准", "合规", "要求", "应", "宜", "不得", "必须", "如何设置", "如何布置"),
    )
    spatial_signal = _contains_any(
        lower,
        ("通视", "邻近", "邻接", "分区", "洁污", "医患", "流线", "出入口", "感染控制", "防护", "隐私", "空间", "排布"),
    )
    design_signal = _contains_any(
        lower,
        ("如何", "设计", "平衡", "布置", "组织", "解决", "回应", "优化", "设置"),
    )
    empirical_signal = _contains_any(lower, ("案例", "经验", "实证", "研究", "论文", "报告"))
    cross_department = len(medical_domains) >= 2 or text.count("、") >= 3

    requires_code_spec = bool(normative_signal or has_numeric or "zoning" in constraint_types or "circulation" in constraint_types)
    requires_design_translation = bool(design_signal and (spatial_signal or requires_code_spec))
    requires_spatial_reference = bool(spatial_signal or (design_signal and medical_domains))
    requires_empirical_reasoning = bool(empirical_signal and not requires_code_spec)

    return QuestionProfile(
        requires_code_spec=requires_code_spec,
        requires_design_translation=requires_design_translation,
        requires_spatial_reference=requires_spatial_reference,
        requires_empirical_reasoning=requires_empirical_reasoning,
        requires_cross_department_synthesis=cross_department,
        constraint_types=constraint_types,
        medical_domains=medical_domains,
    )


def build_evidence_plan(profile: QuestionProfile) -> EvidencePlan:
    required: list[str] = []
    optional: list[str] = []

    if profile.requires_code_spec:
        _add_unique(required, "code_spec")
    if profile.requires_empirical_reasoning:
        _add_unique(required, "paper_or_report")

    # Design translation and spatial references improve completeness, but they
    # should not force the synthesizer into conservative/refusal mode when the
    # authoritative answer evidence is already present. Treat them as optional
    # evidence unless the source type explicitly requires that lane.
    if profile.requires_design_translation:
        _add_unique(optional, "guide")
    if profile.requires_spatial_reference:
        _add_unique(optional, "atlas_or_image")

    for lane in ("guide", "atlas_or_image", "paper_or_report"):
        if lane not in required:
            _add_unique(optional, lane)

    return EvidencePlan(
        required_lanes=required,
        optional_lanes=optional,
        minimum_code_spec_evidence=1 if "code_spec" in required else 0,
        requires_lane_audit=True,
    )


def evidence_context_from_metadata(metadata: Dict[str, Any] | None) -> EvidenceContext:
    metadata = metadata or {}
    return EvidenceContext(
        source_type=str(metadata.get("source_type") or "").strip(),
        task_type=str(metadata.get("task_type") or "").strip(),
        difficulty=str(metadata.get("difficulty") or "").strip(),
        question_id=str(metadata.get("question_id") or "").strip(),
    )


def _explicit_code_spec_signal(query: str) -> bool:
    text = query or ""
    lower = text.lower()
    return bool(
        re.search(r"\bgb\s*\d+", lower)
        or _contains_any(
            lower,
            (
                "gb ",
                "gb/t",
                "规范",
                "标准",
                "条文",
                "强制",
                "合规",
                "code compliance",
                "standard compliance",
            ),
        )
    )


def _plan_from_lanes(required: list[str], optional: list[str]) -> EvidencePlan:
    return EvidencePlan(
        required_lanes=required,
        optional_lanes=[lane for lane in optional if lane not in required],
        minimum_code_spec_evidence=1 if "code_spec" in required else 0,
        requires_lane_audit=True,
    )


def build_source_aware_evidence_plan(
    profile: QuestionProfile,
    context: EvidenceContext,
    query: str = "",
) -> EvidencePlan:
    source_type = (context.source_type or "").strip().lower()
    if not source_type:
        return build_evidence_plan(profile)

    required: list[str] = []
    optional: list[str] = []
    explicit_code_spec = _explicit_code_spec_signal(query)

    if source_type == "technical_standard":
        _add_unique(required, "code_spec")
        _extend_unique(optional, ("guide", "atlas_or_image", "paper_or_report"))
        return _plan_from_lanes(required, optional)

    if source_type == "policy_document":
        _add_unique(required, "policy_document")
        if explicit_code_spec:
            _add_unique(required, "code_spec")
        _extend_unique(optional, ("guide", "paper_or_report"))
        if profile.requires_spatial_reference:
            _add_unique(optional, "atlas_or_image")
        return _plan_from_lanes(required, optional)

    if source_type == "academic_paper":
        _add_unique(required, "paper_or_report")
        if explicit_code_spec:
            _add_unique(required, "code_spec")
        _extend_unique(optional, ("guide", "atlas_or_image"))
        return _plan_from_lanes(required, optional)

    if source_type == "book_report":
        _add_unique(required, "book_report")
        if explicit_code_spec:
            _add_unique(required, "code_spec")
        _extend_unique(optional, ("guide", "atlas_or_image", "paper_or_report"))
        return _plan_from_lanes(required, optional)

    return build_evidence_plan(profile)


def build_evidence_plan_for_query(
    query: str,
    metadata: Dict[str, Any] | None = None,
) -> tuple[QuestionProfile, EvidenceContext, EvidencePlan]:
    profile = profile_question(query)
    context = evidence_context_from_metadata(metadata)
    plan = build_source_aware_evidence_plan(profile, context, query)
    return profile, context, plan


def build_authority_evidence_need(
    query: str,
    profile: QuestionProfile,
    plan: EvidencePlan,
) -> AuthorityEvidenceNeed:
    domain_terms: list[str] = []
    constraint_terms: list[str] = []
    claim_scopes: list[str] = []

    for domain in profile.medical_domains:
        _extend_unique(domain_terms, DOMAIN_AUTHORITY_TERMS.get(domain, ()))
    for constraint in profile.constraint_types:
        _extend_unique(constraint_terms, CONSTRAINT_AUTHORITY_TERMS.get(constraint, ()))
        _extend_unique(claim_scopes, CONSTRAINT_CLAIM_SCOPES.get(constraint, ()))

    role_need_lanes = list(plan.required_lanes)
    for lane in plan.optional_lanes:
        if lane in {"guide", "atlas_or_image"} and lane not in role_need_lanes:
            role_need_lanes.append(lane)

    if "code_spec" in role_need_lanes:
        _add_unique(claim_scopes, "normative_requirement")
    if "policy_document" in role_need_lanes:
        _add_unique(claim_scopes, "policy_requirement")
    if "book_report" in role_need_lanes:
        _add_unique(claim_scopes, "design_translation")
    if "atlas_or_image" in role_need_lanes:
        _add_unique(claim_scopes, "spatial_layout")
    if "guide" in role_need_lanes:
        _add_unique(claim_scopes, "design_translation")

    search_terms: list[str] = []
    _extend_unique(search_terms, domain_terms)
    _extend_unique(search_terms, constraint_terms)
    _extend_unique(search_terms, ("规范", "标准", "条文") if "code_spec" in role_need_lanes else ())
    if "policy_document" in role_need_lanes:
        _extend_unique(search_terms, ("政策", "规划", "指导原则", "实施方案"))
    if "guide" in role_need_lanes:
        _extend_unique(search_terms, ("指南", "手册"))
    if "book_report" in role_need_lanes:
        _extend_unique(search_terms, ("医院建筑设计指南", "书籍", "专著"))
    if "atlas_or_image" in role_need_lanes:
        _extend_unique(search_terms, ("图集", "详图", "平面"))

    base = (query or "").strip()
    if base:
        search_terms.insert(0, base)

    return AuthorityEvidenceNeed(
        required_roles=list(plan.required_lanes),
        optional_roles=list(plan.optional_lanes),
        domain_terms=domain_terms,
        constraint_terms=constraint_terms,
        claim_scopes=claim_scopes,
        search_terms=deduplicate_text_terms(search_terms),
    )


def _metadata_text(data: Dict[str, Any]) -> str:
    metadata = data.get("metadata") or {}
    parts = [
        data.get("source"),
        data.get("doc_category"),
        data.get("document_path"),
        data.get("file_path"),
        data.get("location"),
        data.get("content_type"),
        data.get("source_role"),
    ]
    if isinstance(metadata, dict):
        parts.extend(
            [
                metadata.get("doc_category"),
                metadata.get("source_type"),
                metadata.get("document_type"),
                metadata.get("file_path"),
            ]
        )
    return " ".join(str(part or "") for part in parts).lower()


def _citation_anchor(data: Dict[str, Any]) -> str:
    parts = [
        data.get("source") or data.get("doc_title") or data.get("document_name"),
        data.get("location"),
        data.get("chapter") or data.get("chapter_title"),
        data.get("page_number"),
        data.get("chunk_id"),
    ]
    return " / ".join(str(part) for part in parts if part not in (None, "", []))


def _chunk_doc_title(chunk: Dict[str, Any]) -> str:
    return str(
        chunk.get("source")
        or chunk.get("doc_title")
        or chunk.get("source_document")
        or chunk.get("document_name")
        or ""
    ).strip()


def _chunk_text(chunk: Dict[str, Any]) -> str:
    return str(
        chunk.get("chunk_text")
        or chunk.get("snippet")
        or chunk.get("highlight_text")
        or chunk.get("text")
        or ""
    )


def _authority_content_type(chunk: Dict[str, Any]) -> str:
    explicit = str(chunk.get("content_type") or "").lower()
    if chunk.get("image_url") or explicit == "image":
        return "figure"
    section = str(chunk.get("section") or chunk.get("location") or "").lower()
    text = _chunk_text(chunk)
    if "表" in section or text.lstrip().startswith("[表格]"):
        return "table"
    if classify_source_role({"source": _chunk_doc_title(chunk), "doc_category": chunk.get("doc_category")}) == "code_spec":
        return "clause"
    return explicit or "text"


def _extract_matching_terms(text: str, mapping: Dict[str, Iterable[str]]) -> list[str]:
    matched: list[str] = []
    lower = (text or "").lower()
    for terms in mapping.values():
        for term in terms:
            if str(term).lower() in lower:
                _add_unique(matched, str(term))
    return matched


def _page_number_from_chunk(chunk: Dict[str, Any]) -> int | str | None:
    page_range = chunk.get("page_range")
    if isinstance(page_range, list) and page_range:
        return page_range[0]
    return chunk.get("page_number") or chunk.get("page")


def build_authority_records(chunks: Iterable[Dict[str, Any]]) -> list[AuthorityEvidenceRecord]:
    records: list[AuthorityEvidenceRecord] = []
    for index, chunk in enumerate(chunks or [], start=1):
        if not isinstance(chunk, dict):
            continue
        doc_title = _chunk_doc_title(chunk)
        text = _chunk_text(chunk)
        combined = " ".join(str(part or "") for part in (doc_title, chunk.get("doc_category"), chunk.get("section"), text))
        source_role = classify_source_role(
            {
                "source": doc_title,
                "doc_category": chunk.get("doc_category"),
                "document_path": chunk.get("document_path"),
                "file_path": chunk.get("file_path"),
                "content_type": chunk.get("content_type"),
                "image_url": chunk.get("image_url"),
                "snippet": text,
                "metadata": chunk.get("metadata"),
            }
        )
        if source_role == "inference_context":
            continue

        citation = {
            "source": doc_title,
            "snippet": text,
            "content_type": chunk.get("content_type"),
            "image_url": chunk.get("image_url"),
            "doc_category": chunk.get("doc_category"),
            "location": chunk.get("section") or chunk.get("location"),
        }
        claim_scopes = infer_claim_scopes(citation)
        if source_role == "atlas_or_image":
            _add_unique(claim_scopes, "spatial_layout")
        if source_role == "code_spec":
            _add_unique(claim_scopes, "normative_requirement")
        if source_role == "policy_document":
            _add_unique(claim_scopes, "policy_requirement")
        if source_role == "book_report":
            _add_unique(claim_scopes, "design_translation")

        record_id = str(chunk.get("chunk_id") or chunk.get("id") or f"record-{index}")
        records.append(
            AuthorityEvidenceRecord(
                record_id=record_id,
                source_role=source_role,
                doc_title=doc_title,
                text=text,
                content_type=_authority_content_type(chunk),
                anchor=str(chunk.get("section") or chunk.get("location") or "") or None,
                page_number=_page_number_from_chunk(chunk),
                chunk_id=chunk.get("chunk_id"),
                domain_terms=_extract_matching_terms(combined, DOMAIN_AUTHORITY_TERMS),
                constraint_terms=_extract_matching_terms(combined, CONSTRAINT_AUTHORITY_TERMS),
                claim_scopes=claim_scopes,
                citation=dict(chunk),
            )
        )
    return records


def _term_overlap_score(required: Iterable[str], actual: Iterable[str]) -> int:
    required_set = {str(value).lower() for value in required or [] if str(value).strip()}
    actual_set = {str(value).lower() for value in actual or [] if str(value).strip()}
    return len(required_set & actual_set)


def _record_rank_score(need: AuthorityEvidenceNeed, record: AuthorityEvidenceRecord) -> tuple[int, int, int, int, int, str]:
    role_score = AUTHORITY_BY_ROLE.get(record.source_role, 0)
    if record.source_role in need.required_roles:
        role_score += 40
    elif record.source_role in need.optional_roles:
        role_score += 10
    if record.source_role == "atlas_or_image" and "atlas_or_image" in need.required_roles:
        role_score += 25
    if record.source_role in {"guide", "atlas_or_image"} and any(
        scope in need.claim_scopes for scope in ("spatial_layout", "design_translation")
    ):
        role_score += 12
    if record.source_role == "atlas_or_image" and any(
        term in " ".join(record.constraint_terms).lower() for term in ("隐私", "流线", "布置", "平面", "布局", "通视")
    ):
        role_score += 8

    claim_score = _term_overlap_score(need.claim_scopes, record.claim_scopes) * 20
    constraint_score = _term_overlap_score(need.constraint_terms, record.constraint_terms) * 8
    domain_score = _term_overlap_score(need.domain_terms, record.domain_terms) * 5
    anchor_score = 5 if record.anchor or record.page_number or record.chunk_id else 0
    return (role_score, claim_score, constraint_score, domain_score, anchor_score, record.record_id)


def rank_authority_records(
    need: AuthorityEvidenceNeed,
    records: Iterable[AuthorityEvidenceRecord],
    *,
    limit: int | None = None,
) -> list[AuthorityEvidenceRecord]:
    ranked = sorted(
        list(records or []),
        key=lambda record: _record_rank_score(need, record),
        reverse=True,
    )
    if limit is None:
        return ranked
    return ranked[: max(0, int(limit))]


def classify_source_role(source: Dict[str, Any]) -> str:
    text = _metadata_text(source)
    source_name = str(source.get("source") or "").strip().lower()
    location = str(source.get("location") or "").lower()
    doc_category = str(source.get("doc_category") or "").lower()
    source_type = str(source.get("source_type") or "").lower()
    metadata = source.get("metadata") or {}
    if isinstance(metadata, dict):
        doc_category = f"{doc_category} {str(metadata.get('doc_category') or '').lower()}".strip()
        source_type = f"{source_type} {str(metadata.get('source_type') or '').lower()}".strip()

    if source_name in {"multiple", "unknown", "[unknown]"}:
        return "inference_context"
    if "知识图谱" in location or "community" in location or "kg" in text:
        return "inference_context"
    if "unknown" in text and not any(marker in text for marker in ("gb", "规范", "标准", "指南", "图集", "论文")):
        return "inference_context"

    policy_markers = (
        "政策",
        "规划",
        "指导原则",
        "实施方案",
        "国家医学中心",
        "区域医疗中心",
        "医疗机构设置规划",
        "公立医院",
        "卫生健康",
    )
    book_markers = ("书籍", "专著", "book")
    if _contains_any(text, policy_markers):
        return "policy_document"
    if "book_report" in source_type or "书籍报告" in doc_category or _contains_any(text, book_markers):
        return "book_report"

    if "gb" in text or "规范" in text or "标准" in text or "standard" in text or "code" in text:
        return "code_spec"
    if "指南" in text or "手册" in text or "guide" in text or "manual" in text:
        return "guide"
    if (
        source.get("image_url")
        or "图集" in text
        or "详图" in text
        or "图示" in text
        or "atlas" in text
        or "detail" in text
        or str(source.get("content_type") or "").lower() == "image"
    ):
        return "atlas_or_image"
    if (
        "论文" in text
        or "研究" in text
        or "报告" in text
        or "案例" in text
        or "paper" in text
        or "report" in text
        or "case" in text
        or "journal" in text
    ):
        return "paper_or_report"
    return "inference_context"


def infer_claim_scopes(citation: Dict[str, Any]) -> list[str]:
    role = classify_source_role(citation)
    text = (
        _metadata_text(citation)
        + " "
        + str(citation.get("snippet") or citation.get("highlight_text") or citation.get("text") or "")
    ).lower()
    scopes: list[str] = []

    def add(scope: str) -> None:
        _add_unique(scopes, scope)

    if role == "code_spec":
        add("normative_requirement")
    elif role == "policy_document":
        add("policy_requirement")
    elif role == "guide":
        add("design_translation")
    elif role == "book_report":
        add("design_translation")
    elif role == "atlas_or_image":
        add("spatial_layout")
        add("image_reference")
    elif role == "paper_or_report":
        add("empirical_inference")
    else:
        add("background_context")
        return scopes

    if re.search(r"\d+\s*(m|mm|cm|㎡|m2|%|h|hour|hours|meter|metre|米|毫米|小时)", text):
        add("numeric_parameter")

    if _contains_any(
        text,
        (
            "通视",
            "视线",
            "隐私",
            "邻近",
            "邻接",
            "布局",
            "平面",
            "排列",
            "空间",
            "房间配置",
            "layout",
            "plan",
            "adjacency",
            "privacy",
            "visibility",
            "room configuration",
        ),
    ):
        add("spatial_layout")

    if _contains_any(
        text,
        (
            "分区",
            "洁污",
            "清污",
            "流线",
            "动线",
            "医患",
            "物流",
            "污物",
            "出入口",
            "交通组织",
            "zoning",
            "clean dirty",
            "flow",
            "circulation",
        ),
    ):
        add("workflow_zoning")

    quantity_terms = (
        "数量",
        "间数",
        "床位数",
        "配置数量",
        "规模配置",
        "测算",
        "门诊量",
        "急诊量",
        "手术量",
        "设备台数",
        "quantity",
        "count",
        "bed count",
        "operation volume",
        "outpatient volume",
        "emergency volume",
        "scale configuration",
        "configuration basis",
    )
    dimension_only_terms = (
        "净尺寸",
        "平面净尺寸",
        "长",
        "宽",
        "面积",
        "开间",
        "进深",
        "clear plane dimension",
        "dimension",
        "area requirement",
    )
    if _contains_any(text, quantity_terms):
        add("quantity_configuration")
    elif _contains_any(text, dimension_only_terms):
        pass

    return scopes


def build_evidence_ledger(citations_or_items: Iterable[Dict[str, Any]]) -> EvidenceLedger:
    cards: list[EvidenceCard] = []
    rejected_count = 0
    rejected_reasons: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    for raw in citations_or_items or []:
        if not isinstance(raw, dict):
            rejected_count += 1
            rejected_reasons.append("non-dict evidence")
            continue

        role = classify_source_role(raw)
        if role == "inference_context":
            rejected_count += 1
            rejected_reasons.append("inference_context is not final citable evidence")
            continue

        source = str(raw.get("source") or raw.get("doc_title") or raw.get("document_name") or "").strip()
        snippet = raw.get("snippet") or raw.get("highlight_text") or raw.get("text")
        anchor_key = str(raw.get("chunk_id") or raw.get("page_number") or raw.get("location") or snippet or "")
        key = (role, source, anchor_key)

        if not source or not (snippet or raw.get("chunk_id") or raw.get("location") or raw.get("page_number")):
            rejected_count += 1
            rejected_reasons.append("missing source or usable anchor")
            continue
        if key in seen:
            continue
        seen.add(key)

        cards.append(
            EvidenceCard(
                card_id=f"E{len(cards) + 1}",
                source_role=role,
                authority_level=AUTHORITY_BY_ROLE.get(role, 0),
                source=source,
                location=raw.get("location"),
                page_number=raw.get("page_number"),
                chunk_id=raw.get("chunk_id"),
                snippet=snippet,
                anchor=_citation_anchor(raw),
                claim_scopes=infer_claim_scopes(raw),
                citation=dict(raw),
            )
        )

    return EvidenceLedger(cards=cards, rejected_count=rejected_count, rejected_reasons=rejected_reasons)


def _evidence_scope(role: str) -> tuple[str, str]:
    if role == "code_spec":
        return "hard_constraint", "normative"
    if role == "policy_document":
        return "policy_requirement", "policy"
    if role == "guide":
        return "design_translation", "interpretive"
    if role == "book_report":
        return "design_translation", "reference"
    if role == "atlas_or_image":
        return "spatial_reference", "config_reference"
    if role == "paper_or_report":
        return "empirical_reasoning", "bounded_inference"
    return "low_confidence_context", "not_primary_evidence"


def build_evidence_tiers(citations_or_items: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    tiers: Dict[str, List[Dict[str, Any]]] = {key: [] for key in EVIDENCE_LANES}
    ledger = build_evidence_ledger(citations_or_items)

    for evidence_card in ledger.cards:
        strength, claim_scope = _evidence_scope(evidence_card.source_role)
        card = {
            "card_id": evidence_card.card_id,
            "source_role": evidence_card.source_role,
            "authority_level": evidence_card.authority_level,
            "source": evidence_card.source,
            "location": evidence_card.location,
            "snippet": evidence_card.snippet,
            "chunk_id": evidence_card.chunk_id,
            "page_number": evidence_card.page_number,
            "anchor": evidence_card.anchor,
            "claim_scopes": evidence_card.claim_scopes,
            "support_level": evidence_card.support_level,
            "evidence_strength": strength,
            "claim_scope": claim_scope,
        }
        for extra_key in (
            "content_type",
            "image_url",
            "document_path",
            "file_path",
            "pdf_url",
            "chapter",
            "chapter_title",
            "sub_section",
            "attribute_type",
            "entity",
            "evidence_tier",
        ):
            value = evidence_card.citation.get(extra_key)
            if value not in (None, "", []):
                card[extra_key] = value
        tiers[evidence_card.source_role].append({k: v for k, v in card.items() if v not in (None, "", [])})

    return tiers


_CITATION_RE = re.compile(r"\[(\d+)\]")


def classify_claim_required_scope(claim: str) -> str:
    text = (claim or "").lower()
    if _contains_any(
        text,
        (
            "政策",
            "规划",
            "指导原则",
            "基本原则",
            "需求导向",
            "医疗服务需求",
            "资源配置",
            "principle",
            "policy",
        ),
    ):
        return "policy_requirement"
    if _contains_any(
        text,
        (
            "quantity",
            "count",
            "configure",
            "configuration",
            "scale",
            "bed count",
            "operation volume",
            "outpatient volume",
            "emergency volume",
            "数量",
            "间数",
            "床位数",
            "配置",
            "规模",
            "测算",
            "手术量",
            "门诊量",
            "急诊量",
        ),
    ):
        return "quantity_configuration"
    if re.search(r"\d+\s*(m|mm|cm|㎡|m2|%|h|hour|hours|meter|metre|米|毫米|小时)", text):
        return "normative_requirement" if _contains_any(text, ("should", "shall", "must", "不得", "不宜", "应", "必须")) else "numeric_parameter"
    if _contains_any(text, ("shall", "must", "should", "requirement", "required", "code", "standard", "规范", "标准", "要求", "不得", "不宜", "应", "必须")):
        return "normative_requirement"
    if _contains_any(text, ("flow", "circulation", "zoning", "clean dirty", "流线", "动线", "分区", "洁污", "清污")):
        return "workflow_zoning"
    if _contains_any(text, ("layout", "plan", "adjacency", "privacy", "visibility", "space", "布局", "平面", "邻接", "隐私", "通视", "空间")):
        return "spatial_layout"
    return "design_translation"


def _split_answer_claims(answer: str) -> list[str]:
    """Split an answer into atomic claims for claim-level support auditing.

    See ``_split_answer_claims_with_blocks`` for the full splitting contract.
    This wrapper drops the block ids and returns just the claim strings, for
    callers (and tests) that only need the flat claim list.
    """
    return [claim for claim, _block in _split_answer_claims_with_blocks(answer)]


def _split_answer_claims_with_blocks(answer: str) -> list[tuple[str, int]]:
    """Split an answer into ``(claim, block_id)`` pairs for claim auditing.

    Two properties matter for Chinese benchmark answers:

    1. Chinese sentences are usually joined with no whitespace after the
       terminal punctuation (。！？；). Splitting must rely on the punctuation
       itself, not on a following ``\\s+``.
    2. Sentences without a citation marker are NOT discarded. An uncited
       sentence is a real claim with no supporting source, so it must reach
       ``audit_claim_support`` and be judged there. Dropping it silently is
       what let design inferences escape the audit.

    The ``block_id`` groups sentences that share a single line. Humans cite
    once per point: the lead sentence of a line carries ``[n]`` and follow-up
    sentences on the SAME line continue that point without repeating the
    marker. ``audit_claim_support`` uses the block id to let such a follow-up
    inherit the line's citation, so normal prose structure is not scored as an
    unsupported claim. A newline starts a new block, so a fresh list item does
    NOT inherit the previous item's citation.

    Markdown structure (headers like ``### 简要总结`` and bare list bullets)
    is structural, not substantive, so it is filtered out to avoid inflating
    the unsupported-claim count.
    """
    claims: list[tuple[str, int]] = []
    # Split first into lines (blocks), then into sentences within each line.
    # A citation marker that trails the terminator (e.g. "原则。 [1]") must stay
    # with its sentence, so do not split when the terminator is followed by an
    # optional-space "[n]".
    #
    # Sentences under a boundary/disclaimer heading (回答边界 / 推论边界) describe
    # what was excluded rather than asserting a substantive claim, so they are
    # skipped: keeping them would surface the gate's own disclaimer as an
    # uncited (and therefore unsupported) claim during re-audit.
    in_boundary_section = False
    block_id = 0
    for line in re.split(r"\n+", answer or ""):
        if not line.strip():
            continue
        block_id += 1
        for raw in re.split(r"(?<=[。！？!?；;])(?!\s*\[\d+\])", line):
            claim = raw.strip()
            if not claim:
                continue
            if _is_section_heading(claim):
                in_boundary_section = _is_boundary_heading(claim)
                continue
            if in_boundary_section:
                continue
            if _is_structural_line(claim):
                continue
            claims.append((claim, block_id))
    return claims


_BOUNDARY_HEADING_TERMS = ("回答边界", "推论边界")


def _is_section_heading(text: str) -> bool:
    return text.strip().startswith("#")


def _is_boundary_heading(text: str) -> bool:
    return any(term in text for term in _BOUNDARY_HEADING_TERMS)


def _is_structural_line(text: str) -> bool:
    """Return True for markdown structure that carries no substantive claim."""
    stripped = text.strip()
    if not stripped:
        return True
    # Markdown headers (#, ##, ###...) and horizontal rules.
    if stripped.startswith("#") or set(stripped) <= {"-", "*", "_", " "}:
        return True
    # A bare bullet label with no sentence content, e.g. "- 设计回应" alone.
    body = re.sub(r"^[-*+]\s+", "", stripped)
    if not body:
        return True
    # A colon-terminated lead-in (e.g. "以下仅保留当前证据可直接支持的内容：")
    # introduces a list; it is a label, not an auditable assertion. It carries
    # no citation and would otherwise be scored as an unsupported claim.
    if body.rstrip().endswith(("：", ":")):
        return True
    return False


def _card_supports_scope(card: EvidenceCard, required_scope: str) -> bool:
    if card.source_role == "inference_context":
        return False
    if required_scope == "normative_requirement":
        return card.source_role == "code_spec" and "normative_requirement" in card.claim_scopes
    if required_scope == "policy_requirement":
        return card.source_role == "policy_document" and "policy_requirement" in card.claim_scopes
    # Scope membership is necessary but not sufficient: the card's ROLE must
    # also be authoritative for the scope. A scope token can attach to a card
    # only because its caption text mentions counts or layout words, which is
    # why an atlas caption could otherwise "support" a quantity claim and a
    # paper could "support" a spatial-layout fact.
    if required_scope == "quantity_configuration":
        return (
            card.source_role in _QUANTITY_AUTHORITATIVE_ROLES
            and "quantity_configuration" in card.claim_scopes
        )
    if required_scope == "spatial_layout":
        return (
            card.source_role in _SPATIAL_AUTHORITATIVE_ROLES
            and "spatial_layout" in card.claim_scopes
        )
    return required_scope in card.claim_scopes


# Roles that can authoritatively establish how many of something to provide.
_QUANTITY_AUTHORITATIVE_ROLES = ("code_spec", "policy_document", "book_report")
# Roles that can authoritatively describe spatial arrangement / room layout.
_SPATIAL_AUTHORITATIVE_ROLES = ("atlas_or_image", "guide", "book_report", "code_spec")


def audit_claim_support(answer: str, citations: list[Dict[str, Any]]) -> ClaimSupportAudit:
    ledger = build_evidence_ledger(citations)
    by_index = {idx: card for idx, card in enumerate(ledger.cards, start=1)}
    bindings: list[ClaimBinding] = []

    # Citation inheritance is block-local: within one line, the citation ids
    # carried by an earlier sentence flow forward to a later uncited sentence
    # that continues the same point. A new line (new block) resets the carry,
    # so a fresh list item does not inherit the previous item's citation.
    carried_ids: list[int] = []
    current_block: int | None = None

    for claim, block_id in _split_answer_claims_with_blocks(answer):
        if block_id != current_block:
            current_block = block_id
            carried_ids = []

        own_ids = [int(match.group(1)) for match in _CITATION_RE.finditer(claim)]
        if own_ids:
            carried_ids = own_ids
            citation_ids = own_ids
        else:
            # Inherit the line's running citation context, if any.
            citation_ids = list(carried_ids)

        required_scope = classify_claim_required_scope(claim)
        cards = [by_index[idx] for idx in citation_ids if idx in by_index]
        if not cards:
            bindings.append(
                ClaimBinding(
                    claim=claim,
                    required_scope=required_scope,
                    citation_ids=citation_ids,
                    supported=False,
                    reason="no valid evidence card for citation",
                )
            )
            continue

        supported = any(_card_supports_scope(card, required_scope) for card in cards)
        bindings.append(
            ClaimBinding(
                claim=claim,
                required_scope=required_scope,
                citation_ids=citation_ids,
                supported=supported,
                reason="" if supported else "citation does not support required scope",
            )
        )

    unsupported = [binding for binding in bindings if not binding.supported]
    return ClaimSupportAudit(
        passed=not unsupported,
        bindings=bindings,
        unsupported_claim_count=len(unsupported),
        notes=[binding.reason for binding in unsupported if binding.reason],
    )


def _usable_card(card: Dict[str, Any]) -> bool:
    return bool(card.get("snippet") or card.get("chunk_id") or card.get("location") or card.get("page_number"))


def _cards_for_required_lane(lane: str, tiers: Dict[str, List[Dict[str, Any]]]) -> list[Dict[str, Any]]:
    cards = list(tiers.get(lane) or [])
    if lane == "book_report":
        cards.extend(tiers.get("guide") or [])
    elif lane == "guide":
        cards.extend(tiers.get("book_report") or [])
    return cards


def audit_evidence_coverage(
    plan: EvidencePlan,
    tiers: Dict[str, List[Dict[str, Any]]],
    need: "AuthorityEvidenceNeed | None" = None,
) -> CoverageAudit:
    missing: list[str] = []
    weak: list[str] = []
    notes: list[str] = []

    # Terms the question genuinely needs an authoritative source to address. A
    # required lane that exists but whose cards hit none of these is on-shelf
    # but off-topic (e.g. a code table-of-contents page), so it is marked weak
    # rather than counted as real coverage.
    hit_terms: list[str] = []
    if need is not None:
        _extend_unique(hit_terms, need.domain_terms)
        _extend_unique(hit_terms, need.constraint_terms)

    for lane in plan.required_lanes:
        cards = _cards_for_required_lane(lane, tiers)
        usable = [card for card in cards if _usable_card(card)]
        if not usable:
            missing.append(lane)
            notes.append(f"missing required evidence lane: {lane}")
            continue
        if lane == "code_spec":
            weak_cards = [
                card for card in usable
                if not card.get("snippet") and not (card.get("chunk_id") or card.get("location"))
            ]
            if weak_cards:
                weak.append(lane)
                notes.append("code_spec evidence is weak: lacks snippet and precise location")

        if hit_terms and lane not in weak:
            if not any(_card_hits_terms(card, hit_terms) for card in usable):
                weak.append(lane)
                notes.append(
                    f"required evidence lane '{lane}' does not hit question terms: "
                    + "、".join(hit_terms[:6])
                )

    passed = not missing and not weak
    return CoverageAudit(
        passed=passed,
        missing_required_lanes=missing,
        weak_lanes=weak,
        needs_supplemental_retrieval=bool(missing) or bool(weak),
        notes=notes,
    )


def _card_hits_terms(card: Dict[str, Any], terms: Iterable[str]) -> bool:
    haystack = " ".join(
        str(card.get(key) or "")
        for key in ("snippet", "source", "location", "anchor", "chapter", "chapter_title")
    ).lower()
    return any(str(term).lower() in haystack for term in terms if str(term).strip())


def build_supplemental_lane_queries(
    query: str,
    profile: QuestionProfile,
    audit: CoverageAudit,
) -> dict[str, list[str]]:
    queries: dict[str, list[str]] = {}

    if "code_spec" in audit.missing_required_lanes:
        lane_queries = [
            f"{query} 规范",
            f"{query} 标准",
        ]
        if profile.medical_domains or profile.constraint_types:
            domain_terms = " ".join(profile.medical_domains)
            constraint_terms = " ".join(profile.constraint_types)
            lane_queries.append(f"{domain_terms} {constraint_terms} 条文".strip())
        queries["code_spec"] = [q for q in lane_queries if q.strip()]

    if "guide" in audit.missing_required_lanes:
        queries["guide"] = [f"{query} 设计指南", f"{query} 手册"]
    if "policy_document" in audit.missing_required_lanes:
        queries["policy_document"] = [f"{query} 政策", f"{query} 规划 指导原则 实施方案"]
    if "book_report" in audit.missing_required_lanes:
        queries["book_report"] = [f"{query} 医院建筑设计指南", f"{query} 书籍 专著"]
    if "atlas_or_image" in audit.missing_required_lanes:
        queries["atlas_or_image"] = [f"{query} 图集", f"{query} 详图"]
    if "paper_or_report" in audit.missing_required_lanes:
        queries["paper_or_report"] = [f"{query} 研究", f"{query} 报告"]

    return queries


def build_standards_first_queries(
    query: str,
    profile: QuestionProfile,
    plan: EvidencePlan,
) -> list[str]:
    if "code_spec" not in plan.required_lanes:
        return []

    base = (query or "").strip()
    if not base:
        return []

    need = build_authority_evidence_need(query, profile, plan)
    queries: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if value and value not in queries:
            queries.append(value)

    add(f"{base} 规范 条文 标准")
    add(f"{base} 医院建筑 规范 要求")

    if need.domain_terms or need.constraint_terms:
        add(f"{base} {' '.join(need.domain_terms[:8])} {' '.join(need.constraint_terms[:8])} 规范 条文")
    if need.domain_terms:
        add(f"{' '.join(need.domain_terms[:8])} {' '.join(need.constraint_terms[:8])} 规范 标准".strip())
    if need.claim_scopes:
        add(f"{' '.join(need.domain_terms[:6])} {' '.join(need.constraint_terms[:6])} 医院建筑 条文 要求".strip())

    return queries[:6]
