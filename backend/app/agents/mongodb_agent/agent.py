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

def deduplicate_terms(terms: List[str]) -> List[str]:
    """去重并保持顺序"""
    seen: set[str] = set()
    ordered: List[str] = []
    for term in terms:
        term = term.strip()
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


def _want_images(text: str) -> bool:
    """判断用户是否“明确想要图片/图纸/图示”。尽量用短语而不是单字，避免误触发。"""
    q = (text or "").strip().lower()
    if not q:
        return True
    triggers = [
        "平面图",
        "剖面图",
        "立面图",
        "总平面",
        "图纸",
        "图示",
        "示意图",
        "流程图",
        "结构图",
        "表格截图",
        "配图",
        "附图",
        "带图",
        "带图片",
        "给图",
        "看图",
        "图片",
        "image",
        "figure",
        "diagram",
        "plan",
        "section",
    ]
    if any(neg in q for neg in ("不要图", "不需要图", "不看图", "不要图片", "不需要图片")):
        return False
    return True


def _is_room_norm_query(text: str) -> bool:
    """判断是否属于“某房间/空间的设计规范/要求”类问题（用于多资料兜底）。"""
    q = (text or "").strip()
    if not q:
        return False

    has_room = any(k in q for k in ("手术室", "手术部", "手术间", "洁净手术", "洁净手术部"))
    has_norm = any(k in q for k in ("设计规范", "设计标准", "规范", "标准", "要求", "怎么设计", "布置原则"))
    return bool(has_room and has_norm)


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

    # “规范/要求/布置/配置”类提问，适合补图
    has_norm = any(k in q for k in ("设计规范", "设计标准", "规范", "标准", "要求", "配置", "布置"))
    has_space = any(k in q for k in ("手术室", "手术部", "手术间", "房间", "用房", "空间"))
    return bool(has_norm and has_space)


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


async def _apply_priority_doc_fallback(
    chunks: List[Dict[str, Any]],
    *,
    query: str,
    search_terms: List[str],
    doc_ids: List[str],
    source_documents: List[str],
    retriever: Any,
    doc_distribution: Dict[str, int],
) -> tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    if not chunks or doc_ids or source_documents or (not _is_room_norm_query(query)):
        return chunks, 0, doc_distribution

    existing_dist = _count_doc_distribution(chunks)

    def _norm(s: str) -> str:
        return str(s or "").replace("《", "").replace("》", "").replace(" ", "").lower()

    existing_norm = {_norm(k) for k in existing_dist.keys()}
    need_standard = not any("gb51039" in k or ("综合医院建筑设计" in k and ("规范" in k or "标准" in k)) for k in existing_norm)
    need_atlas = not any("医疗功能房间详图集3" in k or ("详图集" in k and "医疗功能房间" in k) for k in existing_norm)

    priority_terms = deduplicate_terms(
        list(search_terms)
        + [
            "手术室",
            "手术部",
            "洁净手术部",
            "净化",
            "刷手",
            "更衣",
            "缓冲间",
            "无菌",
            "正压",
            "气密",
            "平面",
            "布局",
            "配置",
        ]
    )[:MongoDBAgentConfig.PRIORITY_TERMS_LIMIT]

    async def _search_in_doc(doc_title: str, *, per_doc_limit: int, prefer_images: bool) -> List[Dict[str, Any]]:
        if not doc_title:
            return []
        normalized_title = _norm(doc_title)
        if any(normalized_title in k for k in existing_norm):
            return []
        try:
            candidates = await asyncio.to_thread(
                retriever.search_by_any_keywords,
                priority_terms,
                max(per_doc_limit * 2, 4),
                False,
                None,
                [doc_title],
            )
        except Exception as e:
            logger.info("[MongoDB→Search] PriorityDoc 搜索失败: %s (%s)", doc_title, e)
            return []

        def _rank(ch: Dict[str, Any]) -> tuple[int, int]:
            is_img = bool(ch.get("image_url")) or (ch.get("content_type") == "image")
            page = 10**6
            pr = ch.get("page_range") or []
            if isinstance(pr, list) and pr:
                try:
                    page = int(pr[0])
                except Exception:
                    page = 10**6
            img_score = 1 if is_img else 0
            if prefer_images:
                img_score = 1 if is_img else 0
            else:
                img_score = 1 if (not is_img) else 0
            return (img_score, -page)

        return sorted(candidates or [], key=_rank, reverse=True)[:per_doc_limit]

    tasks: List[asyncio.Future] = []
    if need_standard:
        tasks.append(_search_in_doc("GB 51039-2014 综合医院建筑设计规范.pdf", per_doc_limit=1, prefer_images=False))
        tasks.append(_search_in_doc("GB51039-2014综合医院建筑设计标准.pdf", per_doc_limit=1, prefer_images=False))
    if need_atlas:
        tasks.append(_search_in_doc("医疗功能房间详图集3.pdf", per_doc_limit=2, prefer_images=True))

    if not tasks:
        return chunks, 0, doc_distribution

    results = await asyncio.gather(*tasks, return_exceptions=True)
    priority_added: List[Dict[str, Any]] = []
    for result in results:
        if isinstance(result, list):
            priority_added.extend(result)

    if not priority_added:
        return chunks, 0, doc_distribution

    before = len(chunks)
    chunks = _dedup_chunks_by_id(chunks + priority_added)
    priority_docs_added = max(0, len(chunks) - before)
    doc_distribution = _count_doc_distribution(chunks)
    logger.info("[MongoDB→Search] PriorityDoc 兜底: +%s 条, 资料数=%s", priority_docs_added, len(doc_distribution))
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
    auto_diagrams = _should_auto_include_diagrams(query)
    if not chunks or (not want_images and not auto_diagrams):
        return chunks, 0

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
        if want_images
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
            chunks, used_strategy, retriever_diag = await _execute_keyword_search(
                retriever,
                search_terms,
                query,
                top_k,
                doc_ids,
                source_documents,
            )

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
                "snippet": chunk.get("chunk_text", "")[:150],
                "metadata": metadata,
                "file_path": file_path,
                "document_path": chunk.get("document_path"),
                "pdf_url": pdf_url,  # [FIX 2025-12-16] 与前端/Schema 对齐（snake_case，且不含 /api/v1 前缀）
                "positions": chunk.get("positions", []),
                "doc_id": chunk.get("doc_id"),
                "doc_category": chunk.get("doc_category"),
                "highlight_text": chunk.get("chunk_text", "")[:300],
            }
        ]

        attrs: Dict[str, Any] = {
            "chunk_text": chunk.get("chunk_text", ""),
            "location": location_desc,
            "content_type": content_type,
            "metadata": metadata,
            "document_path": chunk.get("document_path"),
            "file_path": chunk.get("file_path"),
        }

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
