"""Milvus Agent - 优化版本

核心改进：
- ✅ 删除 BaseAgent 类（只保留 graph）
- ✅ 使用 LLMManager（线程安全）
- ✅ 精简代码结构
- ✅ 规范接口（返回 items）
"""

from __future__ import annotations

import asyncio
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
    call_structured_llm,
    get_llm_manager,
)
from backend.app.services.query_expansion import expand_query
from backend.app.services.milvus_chunk_search import get_retriever
from backend.llm_env import get_api_key, get_llm_base_url, get_llm_model, get_model_provider

logger = logging.getLogger("milvus_agent")

DEFAULT_REWRITE_MODEL = os.getenv("MILVUS_AGENT_MODEL") or get_llm_model("gpt-4o-mini")
DEFAULT_TOP_K = 20

_retriever_instance: Any | None = None
_retriever_lock = asyncio.Lock()
_reranker_instance: Any | None = None
_reranker_lock = asyncio.Lock()

try:
    from openai import RateLimitError as OpenAIRateLimitError
except Exception:
    OpenAIRateLimitError = None

try:
    import httpx
    _HTTPX_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)
except Exception:
    _HTTPX_ERRORS = ()


# ============================================================================
# Pydantic 模型
# ============================================================================

class MilvusRewriteResult(BaseModel):
    """LLM 结构化输出：Milvus 查询改写"""

    search_terms: List[str] = Field(
        default_factory=list,
        description="用于向量检索的关键词、短语、同义词或别名，按相关度排序",
    )
    reasoning: str = Field(
        default="",
        description="改写理由",
    )


class KnowledgePoint(BaseModel):
    """LLM 结构化输出：从文本中提取的知识点"""

    title: str = Field(
        description="知识点标题（简洁的一句话概括，如'手术室净高要求'）",
    )
    content: str = Field(
        description="知识点具体内容（可操作的设计规范、技术要求或强制条文）",
    )
    category: str = Field(
        description="知识点类别（如：尺寸要求、功能要求、安全规范、流线设计等）",
    )
    applicable_spaces: List[str] = Field(
        default_factory=list,
        description="适用的空间类型列表（如：手术室、ICU、门诊诊室等）",
    )
    priority: str = Field(
        default="推荐",
        description="优先级（强制、推荐、可选）",
    )
    source_ref: str = Field(
        default="",
        description="来源引用（如：GB 51039-2014 第5.1.2条）",
    )


class KnowledgePointsResult(BaseModel):
    """LLM 结构化输出：知识点列表"""

    knowledge_points: List[KnowledgePoint] = Field(
        default_factory=list,
        description="从文本中提取的知识点列表",
    )


class KnowledgePointsBatchItem(BaseModel):
    """LLM 结构化输出：单个 chunk 的知识点"""

    chunk_id: str = Field(
        description="原始文本片段的 chunk_id",
    )
    knowledge_points: List[KnowledgePoint] = Field(
        default_factory=list,
        description="该 chunk 的知识点列表（无明确规范可为空）",
    )


class KnowledgePointsBatchResult(BaseModel):
    """LLM 结构化输出：批量知识点"""

    items: List[KnowledgePointsBatchItem] = Field(
        default_factory=list,
        description="按 chunk_id 分组的知识点列表",
    )


# ============================================================================
# 状态定义
# ============================================================================

class MilvusState(TypedDict, total=False):
    """Milvus Agent 状态"""
    # 输入
    request: AgentRequest
    query: str

    # 查询改写
    search_terms: List[str]
    rewrite_reason: str

    # 检索结果
    retrieval_results: List[Dict[str, Any]]

    # 知识点提取
    extracted_knowledge_points: List[Dict[str, Any]]  # 提取的结构化知识点

    # 输出
    items: List[AgentItem]
    diagnostics: Dict[str, Any]


# ============================================================================
# LLM 管理
# ============================================================================

def _init_rewrite_llm():
    """初始化查询改写 LLM"""
    api_key = get_api_key()
    if not api_key:
        raise ValueError("缺少 MEDIARCH_API_KEY（milvus_agent）")

    base_url = get_llm_base_url()
    model_provider = get_model_provider()

    # 强制使用 OpenAI 兼容模式（支持第三方 API Gateway）
    base_model = init_chat_model(
        model=DEFAULT_REWRITE_MODEL,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_tokens=12000,  # 2025-11-18: 从400增加到2000，修复LengthFinishReasonError
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
    if "milvus_rewrite" in manager._instances:
        return manager._instances["milvus_rewrite"]

    # ✅ [FIX] 使用asyncio.to_thread()在独立线程中初始化LLM
    try:
        llm = await asyncio.to_thread(_init_rewrite_llm)
        manager._instances["milvus_rewrite"] = llm
        return llm
    except Exception as e:
        logger.warning(f"[MilvusAgent] LLM初始化失败: {e}")
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


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _model_to_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


def _is_transient_error(error: Exception) -> bool:
    """判断是否为瞬时错误（网络、超时、限流等）"""
    if isinstance(error, asyncio.TimeoutError):
        return True
    if OpenAIRateLimitError is not None and isinstance(error, OpenAIRateLimitError):
        return True
    if _HTTPX_ERRORS and isinstance(error, _HTTPX_ERRORS):
        return True
    message = str(error).lower()
    return any(
        keyword in message
        for keyword in (
            "timeout",
            "timed out",
            "temporarily",
            "connection",
            "network",
            "rate limit",
            "quota",
            "overloaded",
            "429",
        )
    )


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _is_agent_rerank_enabled() -> bool:
    # 默认开启；如需关闭可设置 MILVUS_AGENT_ENABLE_RERANK=0
    return _env_bool("MILVUS_AGENT_ENABLE_RERANK", True)


async def get_reranker_async():
    """异步获取 reranker（单例）。"""
    global _reranker_instance
    if _reranker_instance is not None:
        return _reranker_instance
    async with _reranker_lock:
        if _reranker_instance is None:
            from data_process.vector.reranker import BgeReranker

            model_name = os.getenv("RERANKER_MODEL", "qwen3-reranker-8b")
            # Milvus Agent 统一走 API rerank，避免本地大模型加载
            _reranker_instance = await asyncio.to_thread(
                BgeReranker,
                model_name,
                None,
                True,
            )
    return _reranker_instance


async def _rerank_retrieval_results(
    query: str,
    rows: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    对 Milvus 初召回结果做 rerank，返回重排后结果与诊断信息。
    """
    diagnostics: Dict[str, Any] = {
        "enabled": _is_agent_rerank_enabled(),
        "applied": False,
        "reason": "",
        "model": os.getenv("RERANKER_MODEL", "qwen3-reranker-8b"),
    }

    if not diagnostics["enabled"]:
        diagnostics["reason"] = "disabled_by_env"
        return rows, diagnostics
    if len(rows) <= 1:
        diagnostics["reason"] = "insufficient_candidates"
        return rows, diagnostics

    reranker = await get_reranker_async()
    max_candidates = _coerce_int(
        os.getenv("MILVUS_AGENT_RERANK_MAX_CANDIDATES", "80"),
        80,
    )
    max_candidates = max(2, min(max_candidates, 300))
    candidates = rows[:max_candidates]
    tail = rows[max_candidates:]

    chunks: List[Dict[str, Any]] = []
    for idx, row in enumerate(candidates):
        chunks.append(
            {
                "content": row.get("content", "") or "",
                "_candidate_idx": idx,
            }
        )

    ranked_chunks = await asyncio.to_thread(
        reranker.rerank,
        query,
        chunks,
        len(chunks),
    )

    reranked_rows: List[Dict[str, Any]] = []
    used_indices: set[int] = set()
    for item in ranked_chunks:
        idx = item.get("_candidate_idx")
        if idx is None:
            continue
        try:
            pos = int(idx)
        except Exception:
            continue
        if pos < 0 or pos >= len(candidates) or pos in used_indices:
            continue
        used_indices.add(pos)
        row = dict(candidates[pos])
        if "rerank_score" in item:
            row["rerank_score"] = float(item.get("rerank_score") or 0.0)
        reranked_rows.append(row)

    # 兼容上游异常/不完整返回，补齐未出现候选，保持稳定顺序。
    if len(reranked_rows) < len(candidates):
        for idx, row in enumerate(candidates):
            if idx not in used_indices:
                reranked_rows.append(row)

    diagnostics["applied"] = True
    diagnostics["reason"] = "ok"
    diagnostics["input_candidates"] = len(candidates)
    return reranked_rows + tail, diagnostics


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
            max_search_terms=15
        )

        search_terms = result.search_terms
        reasoning = (
            f"QueryExpansion: {len(result.keywords)}个关键词, "
            f"{len(result.synonyms)}个同义词, "
            f"{len(result.ngrams)}个N-gram"
        )

        logger.info(
            f"[Milvus→HeuristicRewrite] "
            f"关键词={result.keywords}, "
            f"同义词={result.synonyms[:3]}..., "
            f"总搜索词={len(search_terms)}"
        )

    except Exception as e:
        # 如果QueryExpansion失败，回退到基础正则方法
        logger.warning(f"[Milvus→HeuristicRewrite] QueryExpansion失败: {e}，使用基础方法")
        cleaned = re.sub(r'[，。,。；;.!？?、\s]+', ' ', query)
        tokens = re.findall(r'[\u4e00-\u9fa5]{2,6}', cleaned)

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


async def get_retriever_async():
    """异步获取 Milvus retriever，避免首次初始化阻塞事件循环"""
    global _retriever_instance
    if _retriever_instance is not None:
        return _retriever_instance
    async with _retriever_lock:
        if _retriever_instance is None:
            _retriever_instance = await asyncio.to_thread(get_retriever)
    return _retriever_instance


def _rebalance_results_by_doc(
    rows: List[Dict[str, Any]],
    limit: int,
    max_per_doc: Optional[int] = None,
    ensure_diversity: bool = True,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    按来源文档做 Round-Robin 重排以提升跨资料覆盖。

    [FIX 2025-12-04] 增强多源平衡机制
    - ensure_diversity=True 时，确保至少从每个文档取 1 条结果（如果有的话）
    - 这样《医疗功能房间详图集3》等资料不会被完全排除

    参数:
        rows: 原始结果列表
        limit: 最大返回数量
        max_per_doc: 每本书最大条数（None=不限制，用轮询）
        ensure_diversity: 是否确保多源多样性

    返回: mixed + distribution
    """
    if not rows:
        return [], {}

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        doc_name = row.get("source_document") or "unknown"
        buckets.setdefault(doc_name, []).append(row)

    # 按桶大小降序排列，但如果ensure_diversity，先确保每个桶至少取一条
    ordered_docs = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
    mixed: List[Dict[str, Any]] = []
    used_docs: set = set()

    # [NEW] 确保多样性：先从每个文档各取一条最高分结果
    if ensure_diversity:
        for doc_name, bucket in ordered_docs:
            if bucket and len(mixed) < limit:
                mixed.append(bucket[0])  # 取最高分的一条
                used_docs.add(doc_name)

    # Round-Robin 轮询填充剩余位置
    max_rounds = max_per_doc if max_per_doc is not None else max(len(b) for _, b in ordered_docs) if ordered_docs else 0

    for round_idx in range(max_rounds):
        for doc_name, bucket in ordered_docs:
            # 如果ensure_diversity，跳过第一轮已取的
            start_idx = 1 if (ensure_diversity and round_idx == 0) else round_idx
            actual_idx = start_idx if ensure_diversity else round_idx

            if actual_idx < len(bucket):
                row = bucket[actual_idx]
                if row not in mixed:
                    mixed.append(row)
                    if len(mixed) >= limit:
                        return mixed, {k: len(v) for k, v in buckets.items()}

    # 如果还不够，继续填充
    if len(mixed) < limit:
        for _, bucket in ordered_docs:
            for row in bucket:
                if row in mixed:
                    continue
                mixed.append(row)
                if len(mixed) >= limit:
                    break
            if len(mixed) >= limit:
                break

    return mixed[:limit], {k: len(v) for k, v in buckets.items()}


async def rewrite_query_with_llm(query: str) -> Optional[MilvusRewriteResult]:
    """
    使用 LLM 改写查询（结构化输出优先 + 兼容兜底）

    [FIX 2025-12-09] 移除 with_structured_output()，改用手动解析
    - 原因：DeepSeek API 与 with_structured_output() 不兼容，导致 JSON 解析失败
    - 修复：使用 llm_output_parser.parse_llm_output() 处理各种格式的 LLM 输出
    """
    from backend.app.utils.llm_output_parser import parse_llm_output

    try:
        llm = await get_rewrite_llm()
    except Exception as e:
        logger.warning(f"[Milvus→Rewrite] 无法获取 LLM: {e}，将使用启发式")
        return None

    system_prompt = (
        "你是一名医院建筑向量检索的查询改写助手。"
        "请分析用户的问题，并构造一组用于 Milvus 向量检索的 search_terms。"
        "search_terms 必须包含原问题的核心实体，还要列出这些实体常见的中英文同义词、别名、缩写。"
        "\n\n**重要：你必须返回有效的 JSON 格式，不要包含任何其他文本。**"
        "\n\n输出格式："
        "\n```json"
        "\n{"
        "\n  \"search_terms\": [\"医技部\", \"医疗技术部\", \"Medical Technology Department\", \"医技部设计\"],"
        "\n  \"reasoning\": \"提取关键词并扩展同义词\""
        "\n}"
        "\n```"
        "\n\n示例："
        "\n问题：医技部设计要点"
        "\n-> {\"search_terms\": [\"医技部\", \"医疗技术部\", \"Medical Technology Department\", \"医技部设计\"], \"reasoning\": \"提取关键词并扩展同义词\"}"
        "\n\n按相关度从高到低排序，最多 8 个词或短语。"
    )

    user_prompt = f"用户问题：{query}\n\n请直接返回 JSON，不要包含其他文本。"

    # 1) Structured Output（带重试）
    max_attempts = 3
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result: MilvusRewriteResult = await call_structured_llm(
                llm=llm,
                pydantic_model=MilvusRewriteResult,
                messages=[
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ],
            )

            logger.info(
                f"[Milvus→Rewrite] LLM 结构化改写成功: "
                f"terms={result.search_terms[:5] if len(result.search_terms) > 5 else result.search_terms}"
            )
            return result
        except Exception as e:
            last_error = e
            if attempt < max_attempts and _is_transient_error(e):
                delay = min(2.0 * attempt, 6.0)
                logger.warning(
                    "[Milvus→Rewrite] 结构化输出瞬时错误，%s/%s 次重试后等待 %.1fs: %s",
                    attempt,
                    max_attempts,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
                continue
            logger.warning("[Milvus→Rewrite] 结构化输出失败，降级为手动解析: %s", e)
            break

    try:
        # [FIX 2025-12-09] LLM 不再绑定 with_structured_output()
        # 需要手动解析返回的内容
        raw_result = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        # 记录原始输出（用于调试）
        if hasattr(raw_result, 'content'):
            logger.debug(f"[Milvus→Rewrite] LLM 原始输出: {raw_result.content[:500]}...")
        else:
            logger.debug(f"[Milvus→Rewrite] LLM 原始输出: {str(raw_result)[:500]}...")

        # 使用通用解析器
        result = parse_llm_output(
            output=raw_result,
            pydantic_model=MilvusRewriteResult,
            fallback_parser=None
        )

        if result:
            logger.info(
                f"[Milvus→Rewrite] LLM 改写成功: "
                f"terms={result.search_terms[:5] if len(result.search_terms) > 5 else result.search_terms}"
            )
            return result
        else:
            logger.warning(f"[Milvus→Rewrite] LLM 输出解析失败，将使用启发式")
            return None

    except Exception as e:
        logger.error(f"[Milvus→Rewrite] LLM 改写异常: {e}，将使用启发式", exc_info=True)
        if last_error is not None:
            logger.debug("[Milvus→Rewrite] Structured Output 最后一次错误: %s", last_error)
        return None


# ============================================================================
# 节点函数
# ============================================================================

async def node_extract_query(state: MilvusState) -> Dict[str, Any]:
    """提取查询"""
    request = state.get("request")
    if request and hasattr(request, "query"):
        query = request.query.strip()
    else:
        query = state.get("query", "").strip()
    
    logger.info(f"[Milvus→ExtractQuery] 查询: {query}")
    
    return {"query": query}


async def node_rewrite_query(state: MilvusState) -> Dict[str, Any]:
    """
    查询改写：扩展关键词和同义词（增强：使用Neo4j扩展 - 2025-01-16）

    优先使用Neo4j Agent提供的扩展实体作为额外查询词
    """
    query = state.get("query", "")
    request = state.get("request")

    if not query:
        return {
            "search_terms": [],
            "rewrite_reason": "空查询，无需改写",
        }

    # ✅ [NEW] 提取Neo4j的扩展信息
    neo4j_expansion = {}
    if request and request.metadata:
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

    # ✅ [NEW] 添加Neo4j扩展的实体作为额外查询词
    if neo4j_expansion and neo4j_expansion.get("expanded_entities"):
        expanded_entity_names = [
            e.get("name", "")
            for e in neo4j_expansion["expanded_entities"][:10]
            if e.get("name")
        ]

        # 合并原有search_terms和Neo4j扩展的实体
        search_terms.extend(expanded_entity_names)
        search_terms = deduplicate_terms(search_terms)

        logger.info(
            f"[Milvus→Rewrite] 使用Neo4j扩展: "
            f"新增 {len(expanded_entity_names)} 个实体, "
            f"总搜索词 {len(search_terms)} 个"
        )
        reasoning += f" + Neo4j扩展({len(expanded_entity_names)}个实体)"

    logger.info(f"[Milvus→Rewrite] 模式={mode}, search_terms={search_terms[:10]}...")

    return {
        "search_terms": search_terms,
        "rewrite_reason": reasoning,
    }


async def node_search_milvus(state: MilvusState) -> Dict[str, Any]:
    """执行 Milvus 向量检索"""
    search_terms = state.get("search_terms") or []
    query = search_terms[0] if search_terms else state.get("query", "")
    original_query = state.get("query", "") or ""
    request = state.get("request")
    
    if not query:
        logger.warning("[Milvus→Search] 空查询")
        return {"retrieval_results": [], "diagnostics": {"error": "empty_query"}}
    
    logger.info(f"[Milvus→Search] 开始搜索，search_terms={search_terms}")
    
    # 获取 retriever（异步避免阻塞）
    try:
        retriever = await get_retriever_async()
    except Exception as e:
        logger.error(f"[Milvus→Search] Retriever 获取失败: {e}")
        return {
            "retrieval_results": [],
            "diagnostics": {"error": f"retriever_init_failed: {e}"},
        }
    
    # 提取参数
    top_k = request.top_k if request else DEFAULT_TOP_K
    top_k = max(1, int(top_k))
    rerank_enabled = _is_agent_rerank_enabled()
    rerank_fetch_multiplier = _coerce_int(
        os.getenv("MILVUS_AGENT_RERANK_FETCH_MULTIPLIER", "3"),
        3,
    )
    rerank_fetch_multiplier = max(1, min(rerank_fetch_multiplier, 10))
    fetch_k = top_k * rerank_fetch_multiplier if rerank_enabled else top_k
    fetch_k = max(top_k, min(fetch_k, 200))
    content_type = request.filters.get("content_type") if request and request.filters else None
    min_score = _coerce_float(
        request.filters.get("min_similarity", 0.0) if request and request.filters else 0.0,
        0.0,
    )

    def _normalize_str_list(value: Any) -> List[str]:
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

    source_documents = _normalize_str_list(
        (request.filters.get("source_documents") if request and request.filters else None)
        or (request.filters.get("source_document") if request and request.filters else None)
    )
    doc_ids = _normalize_str_list(
        (request.filters.get("doc_ids") if request and request.filters else None)
        or (request.filters.get("doc_id") if request and request.filters else None)
    )

    def _normalize_int_list(value: Any) -> List[int]:
        if value is None:
            return []
        if isinstance(value, bool):
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
            out: List[int] = []
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

    def _extract_page_numbers(text: str) -> List[int]:
        text = (text or "").strip()
        if not text:
            return []
        pages: List[int] = []
        # 150-154页 / 150~154页 / 150到154页
        for m in re.finditer(r"(?:第\s*)?(\d{1,4})\s*[-~～到至]\s*(\d{1,4})\s*页", text):
            try:
                a = int(m.group(1))
                b = int(m.group(2))
            except Exception:
                continue
            if a <= 0 or b <= 0:
                continue
            pages.extend([a, b])
        # 单页：152页 / 第152页
        for m in re.finditer(r"(?:第\s*)?(\d{1,4})\s*页", text):
            try:
                pages.append(int(m.group(1)))
            except Exception:
                continue
        # P152 / p152
        for m in re.finditer(r"\b[Pp]\s*(\d{1,4})\b", text):
            try:
                pages.append(int(m.group(1)))
            except Exception:
                continue
        pages = [p for p in pages if 1 <= int(p) <= 10000]
        return list(dict.fromkeys(pages).keys())[:10]

    explicit_page_numbers = _normalize_int_list(request.filters.get("page_numbers") if request and request.filters else None)
    page_numbers = explicit_page_numbers
    if not page_numbers:
        page_numbers = _extract_page_numbers(original_query)

    try:
        page_window = int((request.filters.get("page_window") if request and request.filters else 0) or 0)
    except Exception:
        page_window = 0
    page_window = max(0, min(int(page_window), 10))

    strict_page_filter = (bool(explicit_page_numbers) or bool(re.search(r"(只|仅)返回|仅限|限定|只看", original_query))) and bool(page_numbers)

    # 若用户在问题中用【】明确指定对象（如【职业中毒科】），优先作为“必须命中”的关键词（避免同页其他科室图片混入）
    must_include_terms: List[str] = []
    for m in re.finditer(r"【([^】]{1,50})】", original_query):
        term = (m.group(1) or "").strip()
        if not term:
            continue
        # 避免把纯页码/编号当成强约束
        if term.isdigit():
            continue
        must_include_terms.append(term)
    must_include_terms = list(dict.fromkeys(must_include_terms).keys())[:5]

    def _match_page(value: Any) -> bool:
        if not page_numbers:
            return True
        try:
            pn = int(value)
        except Exception:
            return False
        for p in page_numbers:
            if abs(pn - int(p)) <= page_window:
                return True
        return False

    def _filter_rows_by_pages(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not page_numbers:
            return rows or []
        return [r for r in (rows or []) if _match_page(r.get("page_number"))]

    def _filter_rows_by_terms(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not must_include_terms:
            return rows or []
        filtered: List[Dict[str, Any]] = []
        for r in rows or []:
            hay = f"{r.get('section') or ''}\n{r.get('content') or ''}"
            if any(t in hay for t in must_include_terms):
                filtered.append(r)
        return filtered

    logger.info(
        f"[Milvus→Search] 参数：top_k={top_k}, fetch_k={fetch_k}, content_type={content_type}, min_score={min_score}, "
        f"source_documents={len(source_documents)}, doc_ids={len(doc_ids)}, rerank_enabled={rerank_enabled}"
    )

    def _want_images(text: str) -> bool:
        if content_type:  # 调用方显式过滤时不额外注入
            return False
        q = (text or "").strip()
        if not q:
            return False
        # 尽量用“短语”而不是单字“图”，避免误触发
        phrases = [
            "平面图",
            "剖面图",
            "立面图",
            "详图",
            "示意图",
            "图纸",
            "图片",
            "配图",
            "图示",
        ]
        return any(p in q for p in phrases)
    
    # 依次尝试 search_terms
    results: List[Dict[str, Any]] = []
    used_term = query
    doc_distribution: Dict[str, int] = {}
    terms_to_try = search_terms or [query]
    
    for idx, term in enumerate(terms_to_try, 1):
        term = term.strip()
        if not term:
            continue
        
        try:
            logger.info(f"[Milvus→Search] 第 {idx}/{len(terms_to_try)} 轮：尝试 '{term}'")
            
            # ✅ 使用 asyncio.to_thread 避免阻塞事件循环
            candidate_text = await asyncio.to_thread(
                retriever.search_chunks,
                query=term,
                k=fetch_k,
                content_type=content_type,
                source_documents=source_documents or None,
                doc_ids=doc_ids or None,
                min_similarity=min_score,
            )

            # 额外拉取少量 image chunks（让“要图”的问题更容易返回图片）
            candidate_images: List[Dict[str, Any]] = []
            if _want_images(term):
                # 当问题明确“指定页码/章节找图”时，放大 img_k，提高召回，随后再按页码二次过滤。
                if page_numbers:
                    img_k = max(20, min(200, max(int(top_k) * 4, 50)))
                else:
                    img_k = max(2, min(20, max(int(fetch_k) // 3, 2)))
                candidate_images = await asyncio.to_thread(
                    retriever.search_chunks,
                    query=term,
                    k=img_k,
                    content_type="image",
                    source_documents=source_documents or None,
                    doc_ids=doc_ids or None,
                    min_similarity=min_score,
                )

            # 合并去重（按 chunk_id）
            candidate: List[Dict[str, Any]] = []
            seen_chunk_ids: set[str] = set()
            for row in list(candidate_text or []) + list(candidate_images or []):
                cid = str(row.get("chunk_id") or "").strip()
                if not cid or cid in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(cid)
                candidate.append(row)

            # 如果用户/filters 指定页码，则优先按页码过滤（严格模式下，不接受非目标页结果）
            if page_numbers:
                filtered_candidate = _filter_rows_by_pages(candidate)
                term_filtered_candidate = _filter_rows_by_terms(filtered_candidate) if filtered_candidate else []
                if term_filtered_candidate:
                    filtered_candidate = term_filtered_candidate
                if strict_page_filter:
                    candidate = filtered_candidate
                elif filtered_candidate:
                    candidate = filtered_candidate
            
            logger.info(f"[Milvus→Search] 第 {idx} 轮：找到 {len(candidate)} 条")
            
            if candidate:
                results = candidate
                used_term = term
                logger.info(f"[Milvus→Search] 使用 '{term}' 找到结果，停止尝试")
                break
        
        except Exception as e:
            logger.error(f"[Milvus→Search] 使用 '{term}' 搜索失败: {e}")
            continue
    
    # 如果所有词都失败，尝试原始查询
    if not results and query and query not in terms_to_try:
        logger.info(f"[Milvus→Search] 所有词均失败，尝试原始查询：{query}")
        try:
            results = await asyncio.to_thread(
                retriever.search_chunks,
                query=query,
                k=fetch_k,
                content_type=content_type,
                source_documents=source_documents or None,
                doc_ids=doc_ids or None,
                min_similarity=min_score,
            )
            used_term = query
            logger.info(f"[Milvus→Search] 原始查询：找到 {len(results)} 条")
        except Exception as e:
            logger.error(f"[Milvus→Search] 原始查询失败: {e}")
            results = []

    rerank_diagnostics: Dict[str, Any] = {
        "enabled": rerank_enabled,
        "applied": False,
        "reason": "skipped",
        "model": os.getenv("RERANKER_MODEL", "qwen3-reranker-8b"),
    }

    # Rerank：在多源平衡前先做语义重排
    if results and rerank_enabled:
        rerank_query = (original_query or used_term or "").strip()
        if rerank_query:
            try:
                results, rerank_diagnostics = await _rerank_retrieval_results(
                    rerank_query,
                    results,
                )
                logger.info(
                    "[Milvus→Search] rerank applied=%s reason=%s candidates=%s",
                    rerank_diagnostics.get("applied"),
                    rerank_diagnostics.get("reason"),
                    rerank_diagnostics.get("input_candidates"),
                )
            except Exception as e:
                logger.warning("[Milvus→Search] rerank failed, fallback to original order: %s", e)
                rerank_diagnostics = {
                    "enabled": rerank_enabled,
                    "applied": False,
                    "reason": f"error: {e}",
                    "model": os.getenv("RERANKER_MODEL", "qwen3-reranker-8b"),
                }
        else:
            rerank_diagnostics["reason"] = "empty_query"

    # 重新平衡跨资料覆盖：限制同一文档的返回数量
    if results:
        balanced_results, doc_distribution = _rebalance_results_by_doc(
            results,
            limit=top_k,
            max_per_doc=None,  # 不限制单本书条数，仅做轮询混排
            ensure_diversity=True,  # [FIX 2025-12-04] 确保多源多样性
        )

        # 如果用户明确“要图”，确保至少带 1 张图片（若 Milvus 命中过）
        if _want_images(used_term):
            has_image_in_all = any((r.get("content_type") == "image") for r in results)
            has_image_in_balanced = any((r.get("content_type") == "image") for r in balanced_results)
            if has_image_in_all and not has_image_in_balanced:
                for r in results:
                    if r.get("content_type") == "image" and r not in balanced_results:
                        if balanced_results:
                            balanced_results[-1] = r
                        else:
                            balanced_results = [r]
                        break

        if len(balanced_results) < len(results):
            logger.info(
                "[Milvus→Search] 平衡跨资料覆盖: %s → %s 条, 资料数=%s",
                len(results),
                len(balanced_results),
                len(doc_distribution),
            )
        results = balanced_results

    logger.info(f"[Milvus→Search] 搜索完成：使用 '{used_term}' 找到 {len(results)} 条结果")
    
    return {
        "retrieval_results": results,
        "diagnostics": {
            "result_count": len(results),
            "content_type": content_type,
            "min_similarity": min_score,
            "search_term": used_term,
            "search_terms": terms_to_try,
            "doc_distribution": doc_distribution,
            "source_documents": source_documents,
            "doc_ids": doc_ids,
            "page_numbers": page_numbers,
            "page_window": page_window,
            "strict_page_filter": strict_page_filter,
            "must_include_terms": must_include_terms,
            "rerank": rerank_diagnostics,
        },
    }


async def node_extract_knowledge_points(state: MilvusState) -> Dict[str, Any]:
    """
    从 Milvus 检索结果中提取结构化知识点

    使用 LLM 从文本片段中提取:
    - 具体的设计规范
    - 技术要求
    - 强制条文
    - 适用空间类型

    这样可以将原始文本片段转换为结构化的、可操作的知识点
    """
    retrieval_results = state.get("retrieval_results", [])
    query = state.get("query", "")
    request = state.get("request")

    enable_kp_extraction = False
    if request and request.metadata:
        enable_kp_extraction = bool(request.metadata.get("enable_knowledge_extraction", False))

    if not enable_kp_extraction:
        logger.info("[Milvus→ExtractKP] 知识点提取已禁用，跳过")
        return {"extracted_knowledge_points": []}

    if not retrieval_results:
        logger.info("[Milvus→ExtractKP] 无检索结果,跳过知识点提取")
        return {"extracted_knowledge_points": []}

    meta = request.metadata if request and request.metadata else {}
    kp_mode = "balanced"
    if isinstance(meta.get("kp_mode"), str):
        kp_mode = meta.get("kp_mode", "balanced").strip().lower()
    elif meta.get("thinking_mode") or meta.get("deep_search") or meta.get("search_mode") == "deep_search":
        kp_mode = "deep"
    elif meta.get("quality_priority") is True:
        kp_mode = "quality"

    mode_defaults = {
        # 质量优先（更细致，但更慢）
        "quality": {
            "top_n": 6,
            "batch_size": 1,
            "max_chars": 1200,
            "min_similarity": 0.0,
            "max_concurrency": 2,
        },
        # 思考模式（未来前端开关）
        "deep": {
            "top_n": 8,
            "batch_size": 1,
            "max_chars": 1400,
            "min_similarity": 0.0,
            "max_concurrency": 2,
        },
        # 默认：兼顾质量与速度（略偏质量）
        "balanced": {
            "top_n": 5,
            "batch_size": 2,
            "max_chars": 900,
            "min_similarity": 0.05,
            "max_concurrency": 3,
        },
        # 速度优先
        "fast": {
            "top_n": 3,
            "batch_size": 3,
            "max_chars": 600,
            "min_similarity": 0.1,
            "max_concurrency": 4,
        },
    }

    defaults = mode_defaults.get(kp_mode, mode_defaults["balanced"])

    min_similarity = _coerce_float(
        os.getenv("MILVUS_KP_MIN_SIMILARITY", str(defaults["min_similarity"])),
        defaults["min_similarity"],
    )
    filtered_results = [
        r for r in retrieval_results
        if float(r.get("similarity", 0.0) or 0.0) >= min_similarity
    ]
    if not filtered_results:
        logger.info("[Milvus→ExtractKP] 无符合相似度阈值的结果,跳过知识点提取")
        return {"extracted_knowledge_points": []}

    logger.info(f"[Milvus→ExtractKP] 开始从 {len(filtered_results)} 条结果中提取知识点")

    top_n = _coerce_int(os.getenv("MILVUS_KP_TOP_N", str(defaults["top_n"])), defaults["top_n"])
    top_results = filtered_results[:max(1, top_n)]

    max_chars = _coerce_int(
        os.getenv("MILVUS_KP_MAX_CHARS", str(defaults["max_chars"])),
        defaults["max_chars"],
    )
    max_chars = max(200, max_chars)

    batch_size = _coerce_int(
        os.getenv("MILVUS_KP_BATCH_SIZE", str(defaults["batch_size"])),
        defaults["batch_size"],
    )
    batch_size = max(1, batch_size)

    max_concurrency = _coerce_int(
        os.getenv("MILVUS_KP_MAX_CONCURRENCY", str(defaults["max_concurrency"])),
        defaults["max_concurrency"],
    )
    max_concurrency = max(1, max_concurrency)
    semaphore = asyncio.Semaphore(max_concurrency)

    logger.info(
        "[Milvus→ExtractKP] 模式=%s, top_n=%s, batch_size=%s, max_chars=%s, min_similarity=%.2f",
        kp_mode,
        top_n,
        batch_size,
        max_chars,
        min_similarity,
    )

    meta_by_chunk: Dict[str, Dict[str, Any]] = {}
    for result in top_results:
        chunk_id = str(result.get("chunk_id") or "").strip()
        if not chunk_id:
            continue
        meta_by_chunk[chunk_id] = {
            "chunk_id": chunk_id,
            "source_document": result.get("source_document", ""),
            "section": result.get("section", ""),
            "page_number": result.get("page_number"),
            "similarity": result.get("similarity", 0.0),
        }

    extracted_points: List[Dict[str, Any]] = []

    def _truncate_content(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    try:
        llm = await get_rewrite_llm()

        async def extract_from_result(idx: int, result: Dict[str, Any]) -> List[Dict[str, Any]]:
            content = _truncate_content(result.get("content", ""))
            if not content or len(content) < 20:
                return []

            source_doc = result.get("source_document", "")
            section = result.get("section", "")
            page_number = result.get("page_number")

            system_prompt = """你是医疗建筑设计领域的专家。你的任务是从给定的文本片段中提取结构化的知识点。

知识点应该是:
1. 具体的、可操作的设计规范或技术要求
2. 包含明确的数值、尺寸或标准
3. 能够直接指导设计工作

如果文本中没有明确的设计规范,返回空数组。"""

            user_prompt = f"""用户查询: {query}

文本来源: {source_doc}
章节: {section or '未知'}
页码: {page_number or '未知'}

文本内容:
{content}

请提取其中的结构化知识点。"""

            async with semaphore:
                # 1) Structured Output（带重试）
                max_attempts = 3
                last_error: Exception | None = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        structured: KnowledgePointsResult = await call_structured_llm(
                            llm=llm,
                            pydantic_model=KnowledgePointsResult,
                            messages=[
                                SystemMessage(content=system_prompt),
                                HumanMessage(content=user_prompt),
                            ],
                        )
                        points_structured = [_model_to_dict(p) for p in structured.knowledge_points]
                        for point in points_structured:
                            point["chunk_id"] = result.get("chunk_id", "")
                            point["source_document"] = source_doc
                            point["section"] = section
                            point["page_number"] = page_number
                            point["similarity"] = result.get("similarity", 0.0)
                        logger.info(
                            f"[Milvus→ExtractKP] 结构化提取成功: 结果 {idx+1}, "
                            f"{len(points_structured)} 个知识点"
                        )
                        return points_structured
                    except Exception as e:
                        last_error = e
                        if attempt < max_attempts and _is_transient_error(e):
                            delay = min(2.0 * attempt, 6.0)
                            logger.warning(
                                "[Milvus→ExtractKP] 结构化输出瞬时错误，%s/%s 次重试后等待 %.1fs: %s",
                                attempt,
                                max_attempts,
                                delay,
                                e,
                            )
                            await asyncio.sleep(delay)
                            continue
                        logger.warning("[Milvus→ExtractKP] 结构化输出失败，降级为手动解析: %s", e)
                        break

                # 2) 手动解析兜底
                try:
                    from backend.app.utils.llm_output_parser import parse_llm_output

                    raw_result = await llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt),
                    ])

                    parsed_result = parse_llm_output(
                        output=raw_result,
                        pydantic_model=KnowledgePointsResult,
                        fallback_parser=None,
                    )

                    if parsed_result and parsed_result.knowledge_points:
                        points_manual = [_model_to_dict(p) for p in parsed_result.knowledge_points]
                        for point in points_manual:
                            point["chunk_id"] = result.get("chunk_id", "")
                            point["source_document"] = source_doc
                            point["section"] = section
                            point["page_number"] = page_number
                            point["similarity"] = result.get("similarity", 0.0)
                        logger.info(
                            f"[Milvus→ExtractKP] 手动解析成功: 结果 {idx+1}, "
                            f"{len(points_manual)} 个知识点"
                        )
                        return points_manual
                    logger.warning(f"[Milvus→ExtractKP] 手动解析失败: 结果 {idx+1}")
                except Exception as e:
                    logger.warning(f"[Milvus→ExtractKP] 手动解析异常: 结果 {idx+1}, 错误: {e}")
                    if last_error is not None:
                        logger.debug("[Milvus→ExtractKP] Structured Output 最后一次错误: %s", last_error)

            return []

        async def extract_from_batch(batch_idx: int, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            snippets: List[str] = []
            for idx, result in enumerate(batch, start=1):
                content = _truncate_content(result.get("content", ""))
                if not content or len(content) < 20:
                    continue
                source_doc = result.get("source_document", "")
                section = result.get("section", "")
                page_number = result.get("page_number")
                chunk_id = str(result.get("chunk_id") or "").strip()
                snippets.append(
                    f"[{idx}] chunk_id: {chunk_id}\n"
                    f"来源: {source_doc or '未知'}\n"
                    f"章节: {section or '未知'}\n"
                    f"页码: {page_number or '未知'}\n"
                    f"内容:\n{content}\n"
                )

            if not snippets:
                return []

            system_prompt = """你是医疗建筑设计领域的专家。你的任务是对多个文本片段分别提取结构化的知识点。

要求:
1. 每个片段输出 1-3 个最重要的知识点
2. 必须是明确、可执行的设计规范或技术要求（包含数值、标准或条件）
3. 如果片段中没有明确规范,对应 knowledge_points 为空数组
4. 输出必须严格符合给定 JSON 结构
"""

            user_prompt = (
                f"用户查询: {query}\n\n"
                "请针对以下片段逐个提取知识点:\n\n"
                + "\n".join(snippets)
            )

            async with semaphore:
                # 1) Structured Output（带重试）
                max_attempts = 3
                last_error: Exception | None = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        structured: KnowledgePointsBatchResult = await call_structured_llm(
                            llm=llm,
                            pydantic_model=KnowledgePointsBatchResult,
                            messages=[
                                SystemMessage(content=system_prompt),
                                HumanMessage(content=user_prompt),
                            ],
                        )
                        points: List[Dict[str, Any]] = []
                        for item in structured.items:
                            chunk_id = str(item.chunk_id or "").strip()
                            for kp in item.knowledge_points:
                                point = _model_to_dict(kp)
                                meta = meta_by_chunk.get(chunk_id, {})
                                point.update(meta)
                                if "chunk_id" not in point:
                                    point["chunk_id"] = chunk_id
                                points.append(point)
                        logger.info(
                            f"[Milvus→ExtractKP] 批量提取成功: 批次 {batch_idx+1}, "
                            f"{len(points)} 个知识点"
                        )
                        return points
                    except Exception as e:
                        last_error = e
                        if attempt < max_attempts and _is_transient_error(e):
                            delay = min(2.0 * attempt, 6.0)
                            logger.warning(
                                "[Milvus→ExtractKP] 批量结构化瞬时错误，%s/%s 次重试后等待 %.1fs: %s",
                                attempt,
                                max_attempts,
                                delay,
                                e,
                            )
                            await asyncio.sleep(delay)
                            continue
                        logger.warning("[Milvus→ExtractKP] 批量结构化输出失败，降级为手动解析: %s", e)
                        break

                # 2) 手动解析兜底
                try:
                    from backend.app.utils.llm_output_parser import parse_llm_output

                    raw_result = await llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt),
                    ])

                    parsed_result = parse_llm_output(
                        output=raw_result,
                        pydantic_model=KnowledgePointsBatchResult,
                        fallback_parser=None,
                    )

                    points: List[Dict[str, Any]] = []
                    if parsed_result and parsed_result.items:
                        for item in parsed_result.items:
                            chunk_id = str(item.chunk_id or "").strip()
                            for kp in item.knowledge_points:
                                point = _model_to_dict(kp)
                                meta = meta_by_chunk.get(chunk_id, {})
                                point.update(meta)
                                if "chunk_id" not in point:
                                    point["chunk_id"] = chunk_id
                                points.append(point)
                        logger.info(
                            f"[Milvus→ExtractKP] 批量手动解析成功: 批次 {batch_idx+1}, "
                            f"{len(points)} 个知识点"
                        )
                        return points
                    logger.warning(f"[Milvus→ExtractKP] 批量手动解析失败: 批次 {batch_idx+1}")
                except Exception as e:
                    logger.warning(f"[Milvus→ExtractKP] 批量手动解析异常: 批次 {batch_idx+1}, 错误: {e}")
                    if last_error is not None:
                        logger.debug("[Milvus→ExtractKP] Structured Output 最后一次错误: %s", last_error)

            return []

        if batch_size <= 1:
            tasks = [extract_from_result(idx, result) for idx, result in enumerate(top_results)]
        else:
            batches = [
                top_results[i:i + batch_size]
                for i in range(0, len(top_results), batch_size)
            ]
            tasks = [extract_from_batch(idx, batch) for idx, batch in enumerate(batches)]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                extracted_points.extend(result)

    except Exception as e:
        logger.error(f"[Milvus→ExtractKP] 知识点提取失败: {e}")
        return {"extracted_knowledge_points": []}

    logger.info(f"[Milvus→ExtractKP] 完成知识点提取,共 {len(extracted_points)} 个")

    return {"extracted_knowledge_points": extracted_points}


async def node_format_results(state: MilvusState) -> Dict[str, Any]:
    """
    格式化 Milvus chunks 结果为 AgentItem

    - 基于 page_number/section 构建 location
    - 输出 chunk_id 作为 citations（供 Knowledge Fusion / MongoDB 回表）
    - 附加提取的知识点信息
    """
    retrieval_results = state.get("retrieval_results", [])
    extracted_knowledge_points = state.get("extracted_knowledge_points", [])

    logger.info(f"[Milvus→Format] 格式化 {len(retrieval_results)} 条结果")

    # 构建 chunk_id -> knowledge_points 的映射
    kp_by_chunk: Dict[str, List[Dict[str, Any]]] = {}
    for kp in extracted_knowledge_points:
        chunk_id = kp.get("chunk_id", "")
        if chunk_id:
            if chunk_id not in kp_by_chunk:
                kp_by_chunk[chunk_id] = []
            kp_by_chunk[chunk_id].append(kp)

    items: List[AgentItem] = []
    for row in retrieval_results:
        source_doc = row.get("source_document", "")
        chunk_id = row.get("chunk_id", "")
        content = row.get("content", "") or ""
        section = row.get("section", "") or ""
        page_number = row.get("page_number")
        content_type = row.get("content_type", "text") or "text"
        similarity = float(row.get("similarity", 0.0) or 0.0)

        location_parts = []
        if page_number:
            location_parts.append(f"{page_number}页")
        if section:
            location_parts.append(section)
        elif source_doc:
            doc_name = source_doc if len(source_doc) <= 30 else source_doc[:30] + "..."
            location_parts.append(doc_name)
        location_desc = "|".join(location_parts) if location_parts else "位置待查"

        citations = [
            {
                "source": source_doc,
                "chunk_id": chunk_id,
                "location": location_desc,
                "page_number": page_number,
                "section": section,
                "content_type": content_type,
                "similarity": similarity,
                "snippet": content[:200] if content else "",
            }
        ]

        attrs: Dict[str, Any] = {
            "source_document": source_doc,
            "doc_id": row.get("doc_id", ""),
            "doc_type": row.get("doc_type", ""),
            "section": section,
            "page_number": page_number,
            "content_type": content_type,
            "similarity": similarity,
            "location": location_desc,
        }

        # 添加提取的知识点
        if chunk_id in kp_by_chunk:
            attrs["knowledge_points"] = kp_by_chunk[chunk_id]

        snippet_text = content
        if content_type == "image":
            snippet_text = f"[图片] {content}"
        if len(snippet_text) > 200:
            snippet_text = snippet_text[:200] + "..."

        items.append(
            AgentItem(
                entity_id=chunk_id,
                name=source_doc,
                label="Chunk",
                score=similarity,
                snippet=snippet_text,
                attrs=attrs,
                citations=citations,
                source="milvus_agent",
            )
        )

    logger.info(f"[Milvus→Format] 完成格式化，生成 {len(items)} 个AgentItem（含位置信息和知识点）")

    return {"items": items}


# ============================================================================
# 构建图
# ============================================================================

def build_milvus_graph():
    """构建 Milvus Agent 图"""
    builder = StateGraph(MilvusState)

    # 添加节点
    builder.add_node("extract_query", node_extract_query)
    builder.add_node("rewrite_query", node_rewrite_query)
    builder.add_node("search", node_search_milvus)
    builder.add_node("extract_knowledge", node_extract_knowledge_points)  # 新增知识点提取节点
    builder.add_node("format", node_format_results)

    # 设置流程
    builder.set_entry_point("extract_query")
    builder.add_edge("extract_query", "rewrite_query")
    builder.add_edge("rewrite_query", "search")
    builder.add_edge("search", "extract_knowledge")  # 搜索后提取知识点
    builder.add_edge("extract_knowledge", "format")  # 提取后格式化
    builder.add_edge("format", END)
    
    logger.info("[Milvus] 图构建完成")
    
    return builder.compile()


# ============================================================================
# 导出图
# ============================================================================

graph = build_milvus_graph()

logger.info("[Milvus] 图已导出（纯 StateGraph 模式）")
