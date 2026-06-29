"""MongoDB Agent - 优化版本

核心改进：
- [DONE] 删除 BaseAgent 类（只保留 graph）
- [DONE] 使用 LLMManager（线程安全）
- [DONE] 精简代码结构
- [DONE] 规范接口（返回 items）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from pydantic import BaseModel, Field

from langgraph.graph import END, StateGraph
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage

from backend.app.agents.base_agent import (
    AgentItem,
    AgentRequest,
    get_llm_manager,
)
from backend.app.services.query_expansion import expand_query, QueryExpansion
from backend.app.services.mongodb_search import get_retriever
from backend.app.agents.evidence_orchestration import (
    AuthorityEvidenceNeed,
    CoverageAudit,
    build_authority_evidence_need,
    build_authority_records,
    build_evidence_plan_for_query,
    build_standards_first_queries,
    build_supplemental_lane_queries,
    rank_authority_records,
)
from backend.llm_env import get_api_key, get_llm_base_url, get_llm_model, get_model_provider

logger = logging.getLogger("mongodb_agent")

DEFAULT_REWRITE_MODEL = os.getenv("MONGODB_AGENT_MODEL") or get_llm_model("gpt-4o-mini")


class MongoDBAgentConfig:
    """MongoDB Agent 配置常量"""
    MAX_SEARCH_TERMS = 15
    MAX_HINT_ENTITIES = 15
    MAX_HINT_SEARCH_TERMS = 10
    MAX_NEO4J_EXPANDED_ENTITIES = 10
    LOG_SEARCH_TERMS = 10
    DEFAULT_TOP_K = 5
    PAGE_WINDOW_MAX = 10
    PRIORITY_TERMS_LIMIT = 12
    IMAGE_K_BASE_MIN = 2
    IMAGE_K_BASE_MAX = 8
    IMAGE_K_WANT_MIN = 5
    IMAGE_K_AUTO_MIN = 2
    HINT_MAX_DOCS = 3
    HINT_MAX_PAGES_PER_DOC = 4
    IMAGE_PER_DOC_MAX = 2
    IMAGE_PAGE_WINDOW_FALLBACK = 1


# 预编译正则
_RE_CLEAN_QUERY = re.compile(r"[，。,。；;.!？?、\s]+")
_RE_TOKEN = re.compile(r"[\u4e00-\u9fa5]{2,6}")
_RE_SECTION_PATTERN_1 = re.compile(r"(第\d+章)\s*([^-]+?)(?:\s*-\s*(\d+\.\d+\s*.+))?$")
_RE_SECTION_PATTERN_2 = re.compile(r"(\d+\.\d+)\s+(.+)")
_RE_SECTION_PATTERN_3 = re.compile(r"(第\d+章)\s+(.+)")
_RE_DOC_PATH_STRIP = re.compile(
    r"^.*?(?:[/\\]backend[/\\]databases[/\\]documents|[/\\]data_process[/\\]documents)[/\\]"
)

# citation snippet 在源头的字符预算。提高到 300，避免正文一进 citation 就被剁碎，
# 与合成器 PROMPT_SNIPPET_CHARS(240)/CODE_SPEC(400) 配合保证正文完整进入 prompt。
CITATION_SNIPPET_CHARS = 300


class MongoDBAgentError(Exception):
    """MongoDB Agent 基础异常"""


class RetrieverInitError(MongoDBAgentError):
    """Retriever 初始化失败"""


class SearchExecutionError(MongoDBAgentError):
    """检索执行失败"""

# ============================================================================
# Pydantic 模型
# ============================================================================

class MongoRewriteResult(BaseModel):
    """LLM 结构化输出：MongoDB 关键词改写"""
    
    search_terms: List[str] = Field(
        default_factory=list,
        description="用于文本搜索的关键词、短语、同义词或别名，按相关度排序",
    )
    reasoning: str = Field(
        default="",
        description="改写理由",
    )


# ============================================================================
# 状态定义
# ============================================================================

class MongoDBState(TypedDict, total=False):
    """MongoDB Agent 状态"""
    # 输入
    request: AgentRequest
    query: str
    
    # 查询改写
    search_terms: List[str]
    rewrite_reason: str
    
    # 检索结果
    retrieval_results: List[Dict[str, Any]]
    
    # 输出
    items: List[AgentItem]
    diagnostics: Dict[str, Any]


# ============================================================================
# LLM 管理
# ============================================================================

def _init_rewrite_llm() -> Any:
    """初始化查询改写 LLM"""
    api_key = get_api_key()
    if not api_key:
        raise ValueError("缺少 MEDIARCH_API_KEY（mongodb_agent）")

    base_url = get_llm_base_url()
    model_provider = get_model_provider()

    # 强制使用 OpenAI 兼容模式（支持第三方 API Gateway）
    base_model = init_chat_model(
        model=DEFAULT_REWRITE_MODEL,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_tokens=12000,
    )

    # [FIX 2025-12-09] 移除 with_structured_output()，改用手动解析
    # 原因：DeepSeek API 与 with_structured_output() 不兼容，导致 JSON 解析失败
    return base_model


async def get_rewrite_llm():
    """
    获取查询改写 LLM（异步版本，修复阻塞调用问题）

    2025-01-16: 使用asyncio.to_thread()包装同步LLM初始化，
    避免LangGraph dev的阻塞调用检测。
    """
    import asyncio

    manager = get_llm_manager()

    # 检查是否已缓存
    if "mongodb_rewrite" in manager._instances:
        return manager._instances["mongodb_rewrite"]

    # [DONE] [FIX] 使用asyncio.to_thread()在独立线程中初始化LLM
    try:
        llm = await asyncio.to_thread(_init_rewrite_llm)
        manager._instances["mongodb_rewrite"] = llm
        return llm
    except Exception as e:
        logger.warning(f"[MongoDBAgent] LLM初始化失败: {e}")
        raise


# ============================================================================
# 辅助函数
# ============================================================================

_INTERNAL_SEARCH_TERM_PATTERNS = (
    re.compile(r"^(?:Space|DesignMethod|FunctionalZone|KnowledgePoint|Source)(?:社区)?$", re.I),
    re.compile(r"^[A-Za-z]+社区$"),
)


def _clean_search_term(term: Any) -> str:
    text = re.sub(r"\s+", " ", str(term or "")).strip(" ：:，,。；;、")
    if not text:
        return ""
    lower = text.lower()
    if lower.endswith((".pdf", ".doc", ".docx", ".xlsx", ".txt")):
        return ""
    if any(pattern.match(text) for pattern in _INTERNAL_SEARCH_TERM_PATTERNS):
        return ""
    if re.search(r"(?:\.pdf|\.docx?|\.xlsx?|\.txt)$", lower):
        return ""
    if len(text) > 48 and not re.search(r"\s", text):
        return ""
    return text


def deduplicate_terms(terms: List[str]) -> List[str]:
    """去重并保持顺序"""
    seen: set[str] = set()
    ordered: List[str] = []
    for term in terms:
        term = _clean_search_term(term)
        if not term or term in seen:
            continue
        seen.add(term)
        ordered.append(term)
    return ordered


def _normalize_str_list(value: Any) -> List[str]:
    """标准化为字符串列表"""
    if value is None:
        return []
    if isinstance(value, str):
        raw = [v.strip() for v in value.split(",")]
        return [v for v in raw if v]
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for v in value:
            s = str(v or "").strip()
            if s:
                out.append(s)
        return out
    s = str(value or "").strip()
    return [s] if s else []


def _normalize_int_list(value: Any) -> List[int]:
    """标准化为整数列表"""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("，", ",").split(",")]
        out: List[int] = []
        for p in parts:
            if not p:
                continue
            try:
                out.append(int(p))
            except Exception:
                continue
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for v in value:
            if v is None or isinstance(v, bool):
                continue
            try:
                out.append(int(v))
            except Exception:
                continue
        return out
    try:
        return [int(value)]
    except Exception:
        return []


def heuristic_rewrite(query: str) -> Dict[str, Any]:
    """
    启发式查询改写（LLM 失败时的兜底）

    [UPGRADED] 2025-11-15: 使用 QueryExpansion 模块进行智能扩展
    - 支持jieba分词
    - 同义词扩展
    - N-gram组合
    - 领域特定别名映射
    """
    try:
        # 使用QueryExpansion模块
        result = expand_query(
            query,
            include_synonyms=True,
            include_ngrams=True,
            max_search_terms=MongoDBAgentConfig.MAX_SEARCH_TERMS  # 增加搜索词数量
        )

        search_terms = result.search_terms
        reasoning = (
            f"QueryExpansion: {len(result.keywords)}个关键词, "
            f"{len(result.synonyms)}个同义词, "
            f"{len(result.ngrams)}个N-gram"
        )

        logger.info(
            f"[MongoDB→HeuristicRewrite] "
            f"关键词={result.keywords}, "
            f"同义词={result.synonyms[:3]}..., "
            f"总搜索词={len(search_terms)}"
        )

    except Exception as e:
        # 如果QueryExpansion失败，回退到基础正则方法
        logger.warning(f"[MongoDB→HeuristicRewrite] QueryExpansion失败: {e}，使用基础方法")
        cleaned = _RE_CLEAN_QUERY.sub(" ", query)
        tokens = _RE_TOKEN.findall(cleaned)

        # 去重
        keywords: List[str] = []
        for token in tokens:
            if token not in keywords:
                keywords.append(token)

        # 回退
        if not keywords and len(query) >= 2:
            keywords = [query[:4]]

        search_terms = keywords[:8]
        reasoning = "启发式：基于中文短语拆分（基础模式）"

    return {
        "search_terms": search_terms,
        "reasoning": reasoning,
    }


def _has_explicit_image_intent(text: str) -> bool:
    """问题是否带显式视觉意图(图纸/示意/空间布置)。用于决定补图配额走 want 档(更多图)。"""
    q = (text or "").strip().lower()
    explicit_diagram_intent = any(
        k in q
        for k in (
            "图纸",
            "图示",
            "示意图",
            "流程图",
            "平面图",
            "剖面图",
            "立面图",
            "总平面",
            "配图",
            "附图",
            "带图",
            "图片",
            "看图",
        )
    )
    spatial_intent = any(k in q for k in ("空间推理", "空间排布", "空间布局", "平面组织", "流线", "通视", "隐私", "分区"))
    return bool(explicit_diagram_intent or spatial_intent)


def _want_images(text: str) -> bool:
    """是否为本次回答补充图片。

    默认开启图文并茂:除非用户显式拒绝图片,否则都补图。
    无关图片由下游相关性过滤(chat.py `_filter_image_refs_for_answer`)兜底剔除。
    """
    q = (text or "").strip().lower()
    if any(neg in q for neg in ("不要图", "不需要图", "不看图", "不要图片", "不需要图片")):
        return False
    return True


EVIDENCE_SOURCE_ROLES = ("code_spec", "guide", "atlas_or_image", "paper_or_report")


def _normalize_numeric_constraint_terms(query: str) -> List[str]:
    """通用数值/单位归一化，避免因 30米/30m 写法不同漏检。"""
    q = query or ""
    terms: List[str] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(米|m|M|毫米|mm|MM|平方米|㎡)", q):
        number, unit = match.group(1), match.group(2).lower()
        if unit == "米":
            terms.extend([f"{number}m", f"{number} m"])
        elif unit == "m":
            terms.extend([f"{number}米"])
        elif unit in {"毫米", "mm"}:
            terms.extend([f"{number}mm", f"{number}毫米"])
        elif unit in {"平方米", "㎡"}:
            terms.extend([f"{number}㎡", f"{number}平方米"])
    return deduplicate_terms(terms)


def _chunk_role_text(chunk: Dict[str, Any]) -> str:
    metadata = chunk.get("metadata") or {}
    return " ".join(
        str(part or "").lower()
        for part in (
            chunk.get("doc_title"),
            chunk.get("source_document"),
            chunk.get("doc_category"),
            chunk.get("file_path"),
            chunk.get("document_path"),
            chunk.get("content_type"),
            metadata.get("category") if isinstance(metadata, dict) else "",
            metadata.get("doc_category") if isinstance(metadata, dict) else "",
        )
    )


def _classify_chunk_evidence_role(chunk: Dict[str, Any]) -> str:
    text = _chunk_role_text(chunk)
    content_type = str(chunk.get("content_type") or "").lower()
    if "gb" in text or "规范" in text or "标准" in text or "標準" in text:
        return "code_spec"
    if "指南" in text or "手册" in text or "guide" in text or "manual" in text:
        return "guide"
    if (
        chunk.get("image_url")
        or content_type == "image"
        or "图集" in text
        or "详图" in text
        or "图示" in text
        or "atlas" in text
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
    return "other"


def _chunk_identity(chunk: Dict[str, Any]) -> str:
    return str(
        chunk.get("chunk_id")
        or "|".join(
            str(part or "")
            for part in (
                chunk.get("doc_id"),
                chunk.get("doc_title") or chunk.get("source_document"),
                chunk.get("page_range"),
                chunk.get("content_type"),
            )
        )
    )


def _select_missing_evidence_role_chunks(
    existing_chunks: List[Dict[str, Any]],
    candidate_chunks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """从通用候选中为缺失资料角色各补一条，不规定最终引用数量。"""
    existing_roles = {
        _classify_chunk_evidence_role(chunk)
        for chunk in existing_chunks or []
    }
    missing_roles = [role for role in EVIDENCE_SOURCE_ROLES if role not in existing_roles]
    if not missing_roles:
        return []

    existing_ids = {_chunk_identity(chunk) for chunk in existing_chunks or []}
    selected: List[Dict[str, Any]] = []
    selected_roles: set[str] = set()
    for chunk in candidate_chunks or []:
        role = _classify_chunk_evidence_role(chunk)
        if role not in missing_roles or role in selected_roles:
            continue
        identity = _chunk_identity(chunk)
        if identity in existing_ids:
            continue
        selected.append(chunk)
        selected_roles.add(role)
        if len(selected_roles) == len(missing_roles):
            break
    return selected


def _role_coverage_search_terms(query: str, search_terms: List[str]) -> List[str]:
    return deduplicate_terms(
        list(search_terms or [])
        + _normalize_numeric_constraint_terms(query)
        + ([query] if query else [])
    )


def _planned_evidence_lane_terms(query: str, metadata: Dict[str, Any] | None = None) -> List[str]:
    profile, _context, plan = build_evidence_plan_for_query(query, metadata)
    planned_lanes = list(plan.required_lanes)
    for lane in plan.optional_lanes:
        if lane not in planned_lanes:
            planned_lanes.append(lane)
    if not planned_lanes:
        return []

    audit = CoverageAudit(
        passed=False,
        missing_required_lanes=planned_lanes,
        needs_supplemental_retrieval=True,
    )
    lane_queries = build_supplemental_lane_queries(query, profile, audit)
    terms: List[str] = []
    for lane, queries in lane_queries.items():
        terms.extend(queries[:2])
        if lane == "code_spec":
            terms.extend(["规范", "标准", "条文"])
        elif lane == "policy_document":
            terms.extend(["政策", "规划", "指导原则", "实施方案"])
        elif lane == "guide":
            terms.extend(["指南", "手册"])
        elif lane == "book_report":
            terms.extend(["医院建筑设计指南", "书籍", "专著"])
        elif lane == "atlas_or_image":
            terms.extend(["图集", "详图"])
        elif lane == "paper_or_report":
            terms.extend(["研究", "报告"])
    return deduplicate_terms(terms)


def _is_r2_request(request: AgentRequest | None) -> bool:
    if not request or not request.metadata:
        return False
    return str(request.metadata.get("retrieval_mode") or "").upper() == "R2"


def _build_search_query_plan(
    query: str,
    search_terms: List[str],
    request: AgentRequest | None,
) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []

    if _is_r2_request(request):
        metadata = request.metadata if request else {}
        profile, evidence_context, evidence_plan = build_evidence_plan_for_query(query, metadata)
        authority_need = build_authority_evidence_need(query, profile, evidence_plan)
        standards_queries = build_standards_first_queries(query, profile, evidence_plan)
        standards_queries = deduplicate_terms(list(standards_queries) + list(authority_need.search_terms[:12]))
        if "code_spec" in evidence_plan.required_lanes and standards_queries:
            plan.append(
                {
                    "lane": "standards_first",
                    "queries": standards_queries,
                    "evidence_context": {
                        "source_type": evidence_context.source_type,
                        "task_type": evidence_context.task_type,
                        "difficulty": evidence_context.difficulty,
                        "question_id": evidence_context.question_id,
                    },
                    "evidence_plan_required_lanes": list(evidence_plan.required_lanes),
                    "authority_evidence_need": {
                        "required_roles": list(authority_need.required_roles),
                        "optional_roles": list(authority_need.optional_roles),
                        "domain_terms": list(authority_need.domain_terms),
                        "constraint_terms": list(authority_need.constraint_terms),
                        "claim_scopes": list(authority_need.claim_scopes),
                        "search_terms": list(authority_need.search_terms),
                    },
                }
            )

    plan.append({"lane": "general", "queries": [query] if query else [], "search_terms": list(search_terms or [])})
    return plan


def _should_auto_include_diagrams(text: str) -> bool:
    """
    即便用户未明确说“要图”，也对“规范 + 空间”类问题补充少量图示资料。

    目标：让回答既有权威条文，也有可落地的布置示例（避免只靠单一文字资料）。
    """
    q = (text or "").strip()
    if not q:
        return False

    # 明确拒绝图片时不补
    if any(k in q for k in ("不要图", "不需要图", "不要图片", "不看图")):
        return False

    # 只有明确的图纸/图示/平面/示意意图才自动补图，避免把所有规范题都拉进视觉噪声
    explicit_diagram_intent = any(
        k in q
        for k in (
            "图纸",
            "图示",
            "示意图",
            "流程图",
            "平面图",
            "剖面图",
            "立面图",
            "总平面",
            "配图",
            "附图",
            "带图",
            "图片",
        )
    )
    has_norm = any(k in q for k in ("设计规范", "设计标准", "规范", "标准", "要求", "配置", "布置"))
    has_space = any(k in q for k in ("手术室", "手术部", "手术间", "房间", "用房", "空间"))
    return bool(explicit_diagram_intent and has_norm and has_space)


def _count_doc_distribution(chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for ch in chunks or []:
        doc_name = (
            ch.get("doc_title")
            or ch.get("source_document")
            or ch.get("doc_category")
            or "unknown"
        )
        dist[doc_name] = dist.get(doc_name, 0) + 1
    return dist


def _summarize_standards_first_hits(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    role_distribution: Dict[str, int] = {}
    doc_titles: List[str] = []
    code_spec_doc_titles: List[str] = []

    for chunk in chunks or []:
        role = _classify_chunk_evidence_role(chunk)
        role_distribution[role] = role_distribution.get(role, 0) + 1
        doc_title = str(chunk.get("doc_title") or chunk.get("source_document") or chunk.get("doc_category") or "").strip()
        if doc_title and doc_title not in doc_titles:
            doc_titles.append(doc_title)
        if role == "code_spec" and doc_title and doc_title not in code_spec_doc_titles:
            code_spec_doc_titles.append(doc_title)

    return {
        "hit_count": len(chunks or []),
        "code_spec_count": role_distribution.get("code_spec", 0),
        "role_distribution": role_distribution,
        "doc_titles": doc_titles,
        "code_spec_doc_titles": code_spec_doc_titles,
    }


def _authority_need_from_dict(data: Dict[str, Any] | None) -> AuthorityEvidenceNeed:
    data = data or {}
    required_roles = list(data.get("required_roles") or [])
    optional_roles = list(data.get("optional_roles") or [])
    if "guide" in optional_roles and "guide" not in required_roles:
        required_roles.append("guide")
    if "atlas_or_image" in optional_roles and "atlas_or_image" not in required_roles:
        required_roles.append("atlas_or_image")
    return AuthorityEvidenceNeed(
        required_roles=required_roles,
        optional_roles=optional_roles,
        domain_terms=list(data.get("domain_terms") or []),
        constraint_terms=list(data.get("constraint_terms") or []),
        claim_scopes=list(data.get("claim_scopes") or []),
        search_terms=list(data.get("search_terms") or []),
    )


def _record_to_chunk_metadata(record: Any) -> Dict[str, Any]:
    return {
        "record_id": record.record_id,
        "source_role": record.source_role,
        "content_type": record.content_type,
        "anchor": record.anchor,
        "domain_terms": list(record.domain_terms),
        "constraint_terms": list(record.constraint_terms),
        "claim_scopes": list(record.claim_scopes),
    }


def _rank_standards_first_chunks(
    chunks: List[Dict[str, Any]],
    authority_evidence_need: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    if not chunks:
        return []

    need = _authority_need_from_dict(authority_evidence_need)
    records = build_authority_records(chunks)
    ranked_records = rank_authority_records(need, records)
    record_by_id = {record.record_id: record for record in ranked_records}
    chunk_by_id = {_chunk_identity(chunk): dict(chunk) for chunk in chunks or []}

    ranked_chunks: List[Dict[str, Any]] = []
    used_ids: set[str] = set()
    for record in ranked_records:
        key = str(record.chunk_id or record.record_id)
        chunk = chunk_by_id.get(key)
        if chunk is None:
            chunk = next((dict(candidate) for candidate in chunks if str(candidate.get("chunk_id") or candidate.get("id") or "") == key), None)
        if chunk is None:
            continue
        used_ids.add(_chunk_identity(chunk))
        chunk["retrieval_lane"] = "standards_first"
        chunk["authority_record"] = _record_to_chunk_metadata(record)
        if record.source_role == "code_spec":
            chunk["evidence_tier"] = "code_spec"
        ranked_chunks.append(chunk)

    for chunk in chunks or []:
        identity = _chunk_identity(chunk)
        if identity in used_ids:
            continue
        copy = dict(chunk)
        copy["retrieval_lane"] = "standards_first"
        if _classify_chunk_evidence_role(copy) == "code_spec":
            copy["evidence_tier"] = "code_spec"
        ranked_chunks.append(copy)

    return ranked_chunks


def _dedup_chunks_by_id(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for ch in chunks or []:
        cid = str(ch.get("chunk_id") or "").strip()
        key = cid or json.dumps(
            {
                "doc": ch.get("doc_title") or ch.get("source_document"),
                "page": (ch.get("page_range") or [None])[0],
                "section": ch.get("section"),
                "image_url": ch.get("image_url"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ch)
    return out


def _collect_doc_page_hints(
    chunks: List[Dict[str, Any]],
    *,
    max_docs: int = 3,
    max_pages_per_doc: int = 4,
) -> List[tuple[str, List[int]]]:
    """从已命中的 chunks 中提取 (doc_id, pages[])，用于补图。"""
    doc_order: List[str] = []
    doc_pages: Dict[str, List[int]] = {}

    for ch in chunks or []:
        doc_id = ch.get("doc_id")
        if not doc_id:
            continue
        doc_id_str = str(doc_id).strip()
        if not doc_id_str:
            continue
        if doc_id_str not in doc_pages:
            doc_pages[doc_id_str] = []
            doc_order.append(doc_id_str)
            if len(doc_order) >= max_docs:
                # 先把 doc 收集够，页码后面仍然可以补
                pass

        page: Optional[int] = None
        page_range = ch.get("page_range") or []
        if isinstance(page_range, list) and page_range:
            try:
                page = int(page_range[0])
            except Exception:
                page = None
        if page is None:
            meta = ch.get("metadata") or {}
            if isinstance(meta, dict):
                meta_page = meta.get("page")
                if isinstance(meta_page, int):
                    page = meta_page
                elif isinstance(meta_page, float):
                    page = int(meta_page)

        if isinstance(page, int):
            pages = doc_pages.get(doc_id_str, [])
            if page not in pages:
                pages.append(page)
                doc_pages[doc_id_str] = pages[:max_pages_per_doc]

    hints: List[tuple[str, List[int]]] = []
    for doc_id in doc_order[:max_docs]:
        hints.append((doc_id, doc_pages.get(doc_id, [])[:max_pages_per_doc]))
    return hints


def _rebalance_chunks_by_doc(
    chunks: List[Dict[str, Any]],
    limit: int,
    max_per_doc: Optional[int] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    按来源文档做 Round-Robin 重排以提升跨资料覆盖，但不再强行限制每本书只返回2条。

    max_per_doc=None 时，按照最长的那本书长度进行轮询（上限仍受 limit 约束）。
    返回:
    - mixed: 重新排序后的 chunks
    - distribution: {doc_name: count}
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for chunk in chunks:
        doc_name = (
            chunk.get("doc_title")
            or chunk.get("source_document")
            or chunk.get("doc_category")
            or "unknown"
        )
        buckets.setdefault(doc_name, []).append(chunk)

    ordered_docs = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
    mixed: List[Dict[str, Any]] = []

    max_rounds = max_per_doc if max_per_doc is not None else max(len(b) for _, b in ordered_docs) if ordered_docs else 0

    # Round-Robin 交替抽取，保证跨资料覆盖，同时保留同一本书的多条命中
    for round_idx in range(max_rounds):
        for _, bucket in ordered_docs:
            if round_idx < len(bucket):
                mixed.append(bucket[round_idx])
                if len(mixed) >= limit:
                    return mixed, {k: len(v) for k, v in buckets.items()}

    # 如未达到limit，再顺序补齐
    if len(mixed) < limit:
        for _, bucket in ordered_docs:
            for chunk in bucket:
                if chunk in mixed:
                    continue
                mixed.append(chunk)
                if len(mixed) >= limit:
                    break
            if len(mixed) >= limit:
                break

    return mixed[:limit], {k: len(v) for k, v in buckets.items()}


def _error_result(stage: str, error: Exception | None = None) -> Dict[str, Any]:
    message = stage if error is None else f"{stage}: {error}"
    return {"retrieval_results": [], "diagnostics": {"error": message, "stage": stage}}


async def _execute_chunk_id_search(retriever: Any, chunk_ids: List[str]) -> List[Dict[str, Any]]:
    ids = [str(cid).strip() for cid in chunk_ids if str(cid).strip()]
    if not ids:
        return []
    return await asyncio.to_thread(retriever.get_chunks_by_ids, ids)


async def _execute_keyword_search(
    retriever: Any,
    search_terms: List[str],
    query: str,
    top_k: int,
    doc_ids: List[str],
    source_documents: List[str],
) -> tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    return await asyncio.to_thread(
        retriever.smart_keyword_search,
        search_terms,
        query,
        top_k,
        doc_ids or None,
        source_documents or None,
    )


async def _execute_standards_first_search(
    retriever: Any,
    standards_queries: List[str],
    *,
    authority_evidence_need: Dict[str, Any] | None,
    top_k: int,
    doc_ids: List[str],
    source_documents: List[str],
) -> List[Dict[str, Any]]:
    if not standards_queries:
        return []

    chunks, _used_strategy, _diag = await _execute_keyword_search(
        retriever,
        standards_queries,
        standards_queries[0],
        max(top_k, MongoDBAgentConfig.DEFAULT_TOP_K),
        doc_ids,
        source_documents,
    )
    return _rank_standards_first_chunks(chunks or [], authority_evidence_need)


async def _apply_graph_source_document_supplement(
    chunks: List[Dict[str, Any]],
    *,
    query: str,
    search_terms: List[str],
    top_k: int,
    retriever: Any,
    unified_hints: Dict[str, Any],
    doc_ids: List[str],
    source_documents: List[str],
) -> tuple[List[Dict[str, Any]], int]:
    """补查 KG 图谱中出现但当前 chunk 结果未覆盖的资料源正文。"""
    if not chunks or doc_ids or source_documents:
        return chunks, 0

    hinted_sources = _normalize_str_list((unified_hints or {}).get("source_documents"))
    if not hinted_sources:
        return chunks, 0

    covered_docs = {
        str(chunk.get("doc_title") or chunk.get("source_document") or "").strip()
        for chunk in chunks
        if str(chunk.get("doc_title") or chunk.get("source_document") or "").strip()
    }
    missing_sources = [source for source in hinted_sources if source not in covered_docs]
    if not missing_sources:
        return chunks, 0

    supplements: List[Dict[str, Any]] = []
    per_doc_limit = max(1, min(2, top_k // max(len(missing_sources), 1)))
    for source in missing_sources[:8]:
        try:
            found, _strategy, _diag = await _execute_keyword_search(
                retriever,
                search_terms or [query],
                query,
                per_doc_limit,
                [],
                [source],
            )
        except Exception as exc:
            logger.info("[MongoDB→Search] KG Source 补查失败 source=%s: %s", source, exc)
            continue
        supplements.extend(found or [])

    if not supplements:
        return chunks, 0

    before = len(chunks)
    merged = _dedup_chunks_by_id(chunks + supplements)
    added = max(0, len(merged) - before)
    if added:
        logger.info(
            "[MongoDB→Search] KG Source 资料补查: +%s 条, hinted_sources=%s, covered_before=%s",
            added,
            len(hinted_sources),
            len(covered_docs),
        )
    return merged, added


async def _apply_priority_doc_fallback(
    chunks: List[Dict[str, Any]],
    *,
    query: str,
    search_terms: List[str],
    top_k: int,
    doc_ids: List[str],
    source_documents: List[str],
    retriever: Any,
    doc_distribution: Dict[str, int],
) -> tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    if not chunks or doc_ids or source_documents:
        return chunks, 0, doc_distribution

    coverage_terms = _role_coverage_search_terms(query, search_terms)[: max(MongoDBAgentConfig.PRIORITY_TERMS_LIMIT, 20)]
    if not coverage_terms:
        return chunks, 0, doc_distribution

    try:
        candidates = await asyncio.to_thread(
            retriever.search_by_any_keywords,
            coverage_terms,
            max(top_k * 8, 40),
            False,
            None,
            None,
        )
    except Exception as e:
        logger.info("[MongoDB→Search] EvidenceRole 覆盖搜索失败: %s", e)
        return chunks, 0, doc_distribution

    role_added = _select_missing_evidence_role_chunks(chunks, candidates)
    if not role_added:
        return chunks, 0, doc_distribution

    before = len(chunks)
    chunks = _dedup_chunks_by_id(chunks + role_added)
    priority_docs_added = max(0, len(chunks) - before)
    doc_distribution = _count_doc_distribution(chunks)
    logger.info(
        "[MongoDB→Search] EvidenceRole 覆盖补充: +%s 条, roles=%s, 资料数=%s",
        priority_docs_added,
        [_classify_chunk_evidence_role(chunk) for chunk in role_added],
        len(doc_distribution),
    )
    return chunks, priority_docs_added, doc_distribution


async def _apply_image_supplement(
    chunks: List[Dict[str, Any]],
    *,
    query: str,
    retriever: Any,
    explicit_page_numbers: List[int],
    explicit_page_window: int,
    top_k: int,
) -> tuple[List[Dict[str, Any]], int]:
    want_images = _want_images(query)
    if not chunks or not want_images:
        return chunks, 0

    # 显式视觉意图走高配额(want 档),默认图文并茂走低配额(auto 档,少量图避免稀释)。
    explicit_intent = _has_explicit_image_intent(query) or _should_auto_include_diagrams(query)

    if explicit_page_numbers and explicit_page_window == 0:
        logger.info("[MongoDB→Search] 检测到 filters.page_numbers 且 page_window=0，跳过补图")
        return chunks, 0

    extra_images: List[Dict[str, Any]] = []
    img_k_base = max(
        MongoDBAgentConfig.IMAGE_K_BASE_MIN,
        min(
            MongoDBAgentConfig.IMAGE_K_BASE_MAX,
            max(int(top_k) // 3, MongoDBAgentConfig.IMAGE_K_BASE_MIN),
        ),
    )
    img_k = (
        max(MongoDBAgentConfig.IMAGE_K_WANT_MIN, img_k_base)
        if explicit_intent
        else min(MongoDBAgentConfig.IMAGE_K_AUTO_MIN, img_k_base)
    )
    hints = _collect_doc_page_hints(
        chunks,
        max_docs=MongoDBAgentConfig.HINT_MAX_DOCS,
        max_pages_per_doc=MongoDBAgentConfig.HINT_MAX_PAGES_PER_DOC,
    )
    existing_chunk_ids = {c.get("chunk_id") for c in chunks if c.get("chunk_id")}

    for doc_id, pages in hints:
        if len(extra_images) >= img_k:
            break
        per_doc = max(1, min(MongoDBAgentConfig.IMAGE_PER_DOC_MAX, img_k - len(extra_images)))
        candidates = await asyncio.to_thread(
            retriever.get_image_chunks_near_pages,
            doc_id,
            pages,
            per_doc,
            explicit_page_window if explicit_page_numbers else MongoDBAgentConfig.IMAGE_PAGE_WINDOW_FALLBACK,
        )
        for img in candidates or []:
            cid = img.get("chunk_id")
            if not cid or cid in existing_chunk_ids:
                continue
            existing_chunk_ids.add(cid)
            extra_images.append(img)
            if len(extra_images) >= img_k:
                break

    if extra_images:
        chunks.extend(extra_images)
        return chunks, len(extra_images)

    return chunks, 0


async def rewrite_query_with_llm(query: str) -> Optional[MongoRewriteResult]:
    """
    使用 LLM 改写查询（增强版 - 2025-12-09）

    [FIX 2025-12-09] 移除 with_structured_output()，改用手动解析
    - 原因：DeepSeek API 与 with_structured_output() 不兼容，导致 JSON 解析失败
    - 修复：使用 llm_output_parser.parse_llm_output() 处理各种格式的 LLM 输出
    """
    from backend.app.utils.llm_output_parser import parse_llm_output

    try:
        llm = await get_rewrite_llm()
    except Exception as e:
        logger.warning(f"[MongoDB→Rewrite] 无法获取 LLM: {e}，将使用启发式")
        return None

    system_prompt = (
        "你是一名医院建筑文档检索的关键词分析助手。"
        "请提取适合 MongoDB 文本搜索的 search_terms（关键词、短语、同义词或别名），按重要性排序。"
        "search_terms 必须覆盖原问题的核心实体及其常见的中英文别名、缩写。"
        "\n\n**重要：你必须返回有效的 JSON 格式，不要包含任何其他文本。**"
        "\n\n输出格式："
        "\n```json"
        "\n{"
        "\n  \"search_terms\": [\"病房\", \"病房单元\", \"Ward\", \"病房设计\"],"
        "\n  \"reasoning\": \"提取关键词并扩展同义词\""
        "\n}"
        "\n```"
        "\n\n示例："
        "\n问题：病房设计"
        "\n-> {\"search_terms\": [\"病房\", \"病房单元\", \"Ward\", \"病房设计\"], \"reasoning\": \"提取关键词并扩展同义词\"}"
        "\n\n最多返回 10 个关键词。"
    )

    user_prompt = f"用户问题：{query}\n\n请直接返回 JSON，不要包含其他文本。"

    try:
        # [FIX 2025-12-09] LLM 不再绑定 with_structured_output()
        # 需要手动解析返回的内容
        raw_result = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        # 记录原始输出（用于调试）
        if hasattr(raw_result, 'content'):
            logger.debug(f"[MongoDB→Rewrite] LLM 原始输出: {raw_result.content[:500]}...")
        else:
            logger.debug(f"[MongoDB→Rewrite] LLM 原始输出: {str(raw_result)[:500]}...")

        # 使用通用解析器
        result = parse_llm_output(
            output=raw_result,
            pydantic_model=MongoRewriteResult,
            fallback_parser=None
        )

        if result:
            logger.info(
                f"[MongoDB→Rewrite] LLM 改写成功: "
                f"terms={result.search_terms[:5] if len(result.search_terms) > 5 else result.search_terms}"
            )
            return result
        else:
            logger.warning(f"[MongoDB→Rewrite] LLM 输出解析失败，将使用启发式")
            return None

    except Exception as e:
        logger.error(f"[MongoDB→Rewrite] LLM 改写异常: {e}，将使用启发式", exc_info=True)
        return None


# ============================================================================
# 节点函数
# ============================================================================

async def node_extract_query(state: MongoDBState) -> Dict[str, Any]:
    """提取查询"""
    request = state.get("request")
    if request and hasattr(request, "query"):
        query = request.query.strip()
    else:
        query = state.get("query", "").strip()
    
    logger.info(f"[MongoDB→ExtractQuery] 查询: {query}")
    
    return {"query": query}


async def node_rewrite_query(state: MongoDBState) -> Dict[str, Any]:
    """
    查询改写：扩展关键词

    2025-11-25 升级：支持 unified_hints（来自 Knowledge Fusion）
    - 优先使用 unified_hints.chunk_ids 进行精确定位
    - 使用 unified_hints.entity_names 扩展搜索词
    - 兼容旧版 neo4j_expansion
    """
    query = state.get("query", "")
    request = state.get("request")

    if not query:
        return {
            "search_terms": [],
            "rewrite_reason": "空查询，无需改写",
        }

    # [DONE] [2025-11-25] 提取 unified_hints（来自 Knowledge Fusion）
    unified_hints = {}
    neo4j_expansion = {}
    if request and request.metadata:
        unified_hints = request.metadata.get("unified_hints", {})
        neo4j_expansion = request.metadata.get("neo4j_expansion", {})

    # 尝试 LLM 改写
    llm_result = await rewrite_query_with_llm(query)

    if llm_result:
        search_terms = deduplicate_terms(llm_result.search_terms or [])
        if not search_terms:
            search_terms = [query]
        reasoning = llm_result.reasoning or "LLM 改写"
        mode = "llm"
    else:
        # 兜底：启发式改写
        fallback = heuristic_rewrite(query)
        search_terms = fallback["search_terms"]
        reasoning = fallback["reasoning"]
        mode = "heuristic"

    # [DONE] [2025-11-25] 优先使用 unified_hints 的实体名作为额外查询词
    if unified_hints and unified_hints.get("entity_names"):
        hint_entity_names = unified_hints["entity_names"][:MongoDBAgentConfig.MAX_HINT_ENTITIES]
        search_terms.extend(hint_entity_names)
        search_terms = deduplicate_terms(search_terms)

        logger.info(
            f"[MongoDB->Rewrite] 使用unified_hints: "
            f"新增 {len(hint_entity_names)} 个实体, "
            f"总搜索词 {len(search_terms)} 个"
        )
        reasoning += f" + unified_hints({len(hint_entity_names)}个实体)"

    # [DONE] [2025-11-25] 使用 unified_hints 的搜索词
    if unified_hints and unified_hints.get("search_terms"):
        hint_search_terms = unified_hints["search_terms"][:MongoDBAgentConfig.MAX_HINT_SEARCH_TERMS]
        search_terms.extend(hint_search_terms)
        search_terms = deduplicate_terms(search_terms)
        reasoning += f" + 融合搜索词({len(hint_search_terms)}个)"

    # 兼容旧版：添加Neo4j扩展的实体作为额外查询词
    elif neo4j_expansion and neo4j_expansion.get("expanded_entities"):
        expanded_entity_names = [
            e.get("name", "")
            for e in neo4j_expansion["expanded_entities"][:MongoDBAgentConfig.MAX_NEO4J_EXPANDED_ENTITIES]
            if e.get("name")
        ]

        # 合并原有search_terms和Neo4j扩展的实体
        search_terms.extend(expanded_entity_names)
        search_terms = deduplicate_terms(search_terms)

        logger.info(
            f"[MongoDB→Rewrite] 使用Neo4j扩展: "
            f"新增 {len(expanded_entity_names)} 个实体, "
            f"总搜索词 {len(search_terms)} 个"
        )
        reasoning += f" + Neo4j扩展({len(expanded_entity_names)}个实体)"

    normalized_constraint_terms = _normalize_numeric_constraint_terms(query)
    if normalized_constraint_terms:
        search_terms.extend(normalized_constraint_terms)
        search_terms = deduplicate_terms(search_terms)
        reasoning += f" + 数值约束归一化({len(normalized_constraint_terms)}个)"

    request = state.get("request")
    planned_lane_terms = _planned_evidence_lane_terms(query, request.metadata if request else None)
    if planned_lane_terms:
        search_terms.extend(planned_lane_terms)
        search_terms = deduplicate_terms(search_terms)
        reasoning += f" + 证据角色检索词({len(planned_lane_terms)}个)"

    logger.info(f"[MongoDB→Rewrite] 模式={mode}, search_terms={search_terms[:MongoDBAgentConfig.LOG_SEARCH_TERMS]}...")

    return {
        "search_terms": search_terms,
        "rewrite_reason": reasoning,
    }


async def node_search_mongodb(state: MongoDBState) -> Dict[str, Any]:
    """
    执行 MongoDB 文本检索

    2025-11-25 升级：支持 unified_hints.chunk_ids 精确定位
    - 优先使用 unified_hints.chunk_ids（来自 Knowledge Fusion）
    - 然后使用 request.filters.chunk_ids（兼容旧版）
    - 最后使用关键词搜索（多轮回退策略）
    """
    search_terms = state.get("search_terms") or []
    query = state.get("query", "")
    request = state.get("request")

    if not query:
        logger.warning("[MongoDB->Search] 空查询")
        return _error_result("empty_query")

    logger.info(
        f"[MongoDB->Search] 开始搜索，search_terms={search_terms[:MongoDBAgentConfig.LOG_SEARCH_TERMS]}... "
        f"(共{len(search_terms)}个)"
    )

    # 获取 retriever（使用 asyncio.to_thread 避免阻塞）
    try:
        retriever = await asyncio.to_thread(get_retriever)
    except Exception as e:
        logger.error(f"[MongoDB->Search] Retriever 获取失败: {e}")
        return _error_result("retriever_init_failed", e)

    # 提取参数
    top_k = request.top_k if request else MongoDBAgentConfig.DEFAULT_TOP_K

    filters = request.filters if request and request.filters else {}
    source_documents = _normalize_str_list(filters.get("source_documents") or filters.get("source_document"))
    doc_ids = _normalize_str_list(filters.get("doc_ids") or filters.get("doc_id"))

    explicit_page_numbers = _normalize_int_list(filters.get("page_numbers"))
    try:
        explicit_page_window = int(filters.get("page_window") or 0)
    except Exception:
        explicit_page_window = 0
    explicit_page_window = max(0, min(explicit_page_window, MongoDBAgentConfig.PAGE_WINDOW_MAX))

    # [DONE] [2025-11-25] 优先从 unified_hints 获取 chunk_ids
    unified_hints = {}
    if request and request.metadata:
        unified_hints = request.metadata.get("unified_hints", {})

    chunk_ids_from_hints = unified_hints.get("chunk_ids", []) if unified_hints else []
    chunk_ids_from_filters = request.filters.get("chunk_ids") if request and request.filters else None

    # 合并 chunk_ids（优先 hints，然后 filters）
    chunk_ids = chunk_ids_from_hints or chunk_ids_from_filters or []

    logger.info(
        f"[MongoDB->Search] 参数：top_k={top_k}, chunk_ids={len(chunk_ids) if chunk_ids else 0}个, "
        f"source_documents={len(source_documents)}, doc_ids={len(doc_ids)}"
    )

    chunks = []
    used_strategy = "none"
    doc_distribution: Dict[str, int] = {}
    retriever_diag: Dict[str, Any] = {}
    images_added = 0
    priority_docs_added = 0
    graph_source_docs_added = 0
    standards_first_added = 0
    standards_first_diagnostics: Dict[str, Any] = _summarize_standards_first_hits([])
    standards_first_retained = 0
    search_query_plan = _build_search_query_plan(query, search_terms, request)

    # 执行搜索
    try:
        if chunk_ids:
            # 模式1：按 chunk_ids 检索
            logger.info(f"[MongoDB→Search] 使用 chunk_ids 模式")
            chunks = await _execute_chunk_id_search(retriever, chunk_ids)
            used_strategy = "chunk_ids"

        else:
            # 模式2：关键词搜索（内置文本索引 + 回退策略）
            logger.info(f"[MongoDB→Search] 使用关键词搜索模式")
            standards_plan = next((entry for entry in search_query_plan if entry.get("lane") == "standards_first"), None)
            standards_chunks = await _execute_standards_first_search(
                retriever,
                standards_plan.get("queries", []) if standards_plan else [],
                authority_evidence_need=standards_plan.get("authority_evidence_need") if standards_plan else None,
                top_k=top_k,
                doc_ids=doc_ids,
                source_documents=source_documents,
            )
            if standards_chunks:
                standards_first_added = len(standards_chunks)
                standards_first_diagnostics = _summarize_standards_first_hits(standards_chunks)
                logger.info("[MongoDB→Search] StandardsFirst 命中: %s 条", standards_first_added)

            chunks, used_strategy, retriever_diag = await _execute_keyword_search(
                retriever,
                search_terms,
                query,
                top_k,
                doc_ids,
                source_documents,
            )
            if standards_chunks:
                chunks = _dedup_chunks_by_id(standards_chunks + chunks)
                standards_first_ids = {_chunk_identity(chunk) for chunk in standards_chunks}
                standards_first_retained = sum(1 for chunk in chunks if _chunk_identity(chunk) in standards_first_ids)
                used_strategy = f"standards_first+{used_strategy}"

        chunks, graph_source_docs_added = await _apply_graph_source_document_supplement(
            chunks,
            query=query,
            search_terms=search_terms,
            top_k=top_k,
            retriever=retriever,
            unified_hints=unified_hints,
            doc_ids=doc_ids,
            source_documents=source_documents,
        )
        if graph_source_docs_added:
            used_strategy = f"{used_strategy}+graph_source_docs"

        # 重新平衡跨资料覆盖：限制同一资料返回数量
        if chunks:
            balanced_chunks, doc_distribution = _rebalance_chunks_by_doc(
                chunks,
                limit=top_k,
                max_per_doc=None,  # 不限制单本书条数，仅做轮询混排
            )
            if len(balanced_chunks) < len(chunks):
                logger.info(
                    "[MongoDB→Search] 平衡跨资料覆盖: %s → %s 条, 资料数=%s",
                    len(chunks),
                    len(balanced_chunks),
                    len(doc_distribution),
                )
            chunks = balanced_chunks

        chunks, priority_docs_added, doc_distribution = await _apply_priority_doc_fallback(
            chunks,
            query=query,
            search_terms=search_terms,
            top_k=top_k,
            doc_ids=doc_ids,
            source_documents=source_documents,
            retriever=retriever,
            doc_distribution=doc_distribution,
        )

        chunks, images_added = await _apply_image_supplement(
            chunks,
            query=query,
            retriever=retriever,
            explicit_page_numbers=explicit_page_numbers,
            explicit_page_window=explicit_page_window,
            top_k=top_k,
        )
        if images_added:
            logger.info("[MongoDB→Search] 补图完成: +%s 张", images_added)
        # 结果统计
        if not chunks:
            logger.warning(f"[MongoDB→Search] [FAIL] 所有策略均失败，返回0条")
        else:
            logger.info(f"[MongoDB→Search] [SUCCESS] 最终策略: {used_strategy}, 结果数: {len(chunks)}")

    except Exception as e:
        logger.error(f"[MongoDB→Search] 搜索失败: {e}")
        return _error_result("search_failed", e)

    # 保存结果
    logger.info(f"[MongoDB→Search] 搜索完成：找到 {len(chunks)} 条结果")

    return {
        "retrieval_results": chunks or [],
        "diagnostics": {
            "result_count": len(chunks),
            "search_terms": search_terms[:MongoDBAgentConfig.LOG_SEARCH_TERMS],
            "strategy_used": used_strategy,
            "doc_distribution": doc_distribution,
            "retriever_attempts": retriever_diag.get("attempts") if retriever_diag else None,
            "images_added": images_added,
            "priority_docs_added": priority_docs_added,
            "graph_source_docs_added": graph_source_docs_added,
            "standards_first_added": standards_first_added,
            "standards_first_retained": standards_first_retained,
            "standards_first_dropped_by_dedup_or_rebalance": max(0, standards_first_added - standards_first_retained),
            "standards_first_diagnostics": standards_first_diagnostics,
            "search_query_plan": search_query_plan,
        },
    }


def _parse_section_hierarchy(section: str) -> tuple[str, str, str]:
    """
    解析section字段，提取章节层级信息

    支持格式示例：
    - "第3章 门诊部设计"
    - "第3章 门诊部设计 - 3.1 功能布局"
    - "3.1 功能布局"
    - "门诊部设计"

    返回: (chapter, chapter_title, sub_section)
    - chapter: "第3章"
    - chapter_title: "门诊部设计"
    - sub_section: "3.1 功能布局"
    """

    if not section or not isinstance(section, str):
        return "", "", ""

    section = section.strip()

    # 模式1: "第X章 标题 - X.Y 小节"
    match = _RE_SECTION_PATTERN_1.match(section)
    if match:
        chapter = match.group(1)  # "第3章"
        chapter_title = match.group(2).strip()  # "门诊部设计"
        sub_section = match.group(3).strip() if match.group(3) else ""  # "3.1 功能布局"
        return chapter, chapter_title, sub_section

    # 模式2: "X.Y 小节标题"（只有小节）
    match = _RE_SECTION_PATTERN_2.match(section)
    if match:
        return "", "", section

    # 模式3: "第X章 标题"（只有章）
    match = _RE_SECTION_PATTERN_3.match(section)
    if match:
        chapter = match.group(1)
        chapter_title = match.group(2).strip()
        return chapter, chapter_title, ""

    # 默认：将整个section视为chapter_title
    return "", section, ""


async def node_format_results(state: MongoDBState) -> Dict[str, Any]:
    """
    格式化结果为 AgentItem

    [UPGRADED] 2025-01-17: 增强位置信息提取
    - 从page_range提取页码
    - 使用_parse_section_hierarchy解析章节层级
    - 支持图片chunk的image_url
    - 构建标准化的location描述: "页码|章节|小节"
    """
    retrieval_results = state.get("retrieval_results", [])

    logger.info(f"[MongoDB→Format] 格式化 {len(retrieval_results)} 条结果")

    items: List[AgentItem] = []
    for chunk in retrieval_results:
        metadata = chunk.get("metadata", {})

        # [DONE] [NEW] 从page_range提取页码（优先使用第一页）
        page_range = chunk.get("page_range", [])
        page_number = page_range[0] if page_range else None

        # [DONE] [NEW] 从section字段解析章节层级
        section_raw = chunk.get("section", "")
        chapter, chapter_title, sub_section = _parse_section_hierarchy(section_raw)

        # [DONE] [NEW] 构建位置描述（按标准格式: 页码|章节|小节）
        location_parts = []
        if page_number:
            location_parts.append(f"{page_number}页")
        if chapter and chapter_title:
            location_parts.append(f"{chapter} {chapter_title}")
        elif chapter_title:  # 只有标题没有章号
            location_parts.append(chapter_title)
        if sub_section:
            location_parts.append(sub_section)

        location_desc = "|".join(location_parts) if location_parts else "位置未知"

        # [DONE] [NEW] 提取图片信息（如果是图片chunk）
        image_url = chunk.get("image_url")
        content_type = chunk.get("content_type", "text")

        # [FIX 2025-12-09] 构建 PDF URL，让前端能够访问 PDF 文件
        pdf_url = None
        file_path = chunk.get("file_path") or chunk.get("document_path")

        # [FIX 2025-12-09] 如果没有 file_path，尝试从 doc_title 或 source_document 推断
        if not file_path:
            doc_title = chunk.get("doc_title") or chunk.get("source_document", "")
            doc_category = chunk.get("doc_category") or chunk.get("source_category", "")

            # 如果 doc_title 是 PDF 文件名，构建完整路径
            if doc_title and doc_title.endswith(".pdf"):
                # 根据 doc_category 确定子目录
                category_map = {
                    "标准规范": "标准规范",
                    "参考论文": "参考论文",
                    "书籍报告": "书籍报告",
                    "政策文件": "政策文件",
                }
                subdir = category_map.get(doc_category, "参考论文")  # 默认使用参考论文
                file_path = f"{subdir}/{doc_title}"

        if file_path:
            # 移除 backend/databases/documents/ 前缀（如果存在），构建相对路径
            from urllib.parse import quote
            # 匹配 backend/databases/documents/ 或 backend\databases\documents\
            relative_path = _RE_DOC_PATH_STRIP.sub("", file_path)
            relative_path = relative_path.replace("\\", "/")
            # 构建 API URL（注意：前端会自动拼接 /api/v1 前缀）
            pdf_url = f"/documents/pdf?path={quote(relative_path)}"

        # [DONE] [NEW] 增强的引用信息
        citations = [
            {
                "chunk_id": chunk.get("chunk_id", ""),
                "source": chunk.get("doc_title", "") or chunk.get("source_document", ""),
                "location": location_desc,
                "page_number": page_number,
                "page_range": page_range,
                "chapter": chapter,
                "chapter_title": chapter_title,
                "sub_section": sub_section,
                "content_type": content_type,
                "image_url": image_url,
                "snippet": chunk.get("chunk_text", "")[:CITATION_SNIPPET_CHARS],
                "metadata": metadata,
                "file_path": file_path,
                "document_path": chunk.get("document_path"),
                "pdf_url": pdf_url,  # [FIX 2025-12-16] 与前端/Schema 对齐（snake_case，且不含 /api/v1 前缀）
                "positions": chunk.get("positions", []),
                "doc_id": chunk.get("doc_id"),
                "doc_category": chunk.get("doc_category"),
                "highlight_text": chunk.get("chunk_text", "")[:400],
            }
        ]
        if chunk.get("retrieval_lane"):
            citations[0]["retrieval_lane"] = chunk.get("retrieval_lane")
        chunk_evidence_role = _classify_chunk_evidence_role(chunk)
        if chunk_evidence_role == "code_spec" and (
            chunk.get("evidence_tier") or chunk.get("retrieval_lane") == "standards_first"
        ):
            citations[0]["evidence_tier"] = "code_spec"

        attrs: Dict[str, Any] = {
            "chunk_text": chunk.get("chunk_text", ""),
            "location": location_desc,
            "content_type": content_type,
            "metadata": metadata,
            "document_path": chunk.get("document_path"),
            "file_path": chunk.get("file_path"),
        }
        if chunk.get("retrieval_lane"):
            attrs["retrieval_lane"] = chunk.get("retrieval_lane")
        if chunk_evidence_role == "code_spec" and (
            chunk.get("evidence_tier") or chunk.get("retrieval_lane") == "standards_first"
        ):
            attrs["evidence_tier"] = "code_spec"

        # [DONE] [NEW] 如果是图片，优先显示图片信息
        if content_type == "image" or image_url:
            snippet_text = f"[图片: {chunk.get('chunk_text', '相关配图')[:100]}]"
            attrs["image_url"] = image_url
        else:
            snippet_text = chunk.get("chunk_text", "")
            if len(snippet_text) > 200:
                snippet_text = snippet_text[:200] + "..."

        items.append(
            AgentItem(
                entity_id=chunk.get("chunk_id", ""),
                name=chunk.get("doc_title", "") or chunk.get("source_document", ""),
                snippet=snippet_text,
                label="Document",
                attrs=attrs,
                citations=citations,
                source="mongodb_agent",
            )
        )

    logger.info(f"[MongoDB→Format] 完成格式化，生成 {len(items)} 个AgentItem（含位置信息）")

    return {"items": items}


# ============================================================================
# 构建图
# ============================================================================

def build_mongodb_graph() -> Any:
    """构建 MongoDB Agent 图"""
    builder = StateGraph(MongoDBState)
    
    # 添加节点
    builder.add_node("extract_query", node_extract_query)
    builder.add_node("rewrite_query", node_rewrite_query)
    builder.add_node("search", node_search_mongodb)
    builder.add_node("format", node_format_results)
    
    # 设置流程
    builder.set_entry_point("extract_query")
    builder.add_edge("extract_query", "rewrite_query")
    builder.add_edge("rewrite_query", "search")
    builder.add_edge("search", "format")
    builder.add_edge("format", END)
    
    logger.info("[MongoDB] 图构建完成")
    
    return builder.compile()


# ============================================================================
# 导出图
# ============================================================================

graph = build_mongodb_graph()

logger.info("[MongoDB] 图已导出（纯 StateGraph 模式）")
