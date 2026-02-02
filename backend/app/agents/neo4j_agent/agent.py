"""Neo4j Agent - 优化版本

核心改进：
- ✅ 删除 BaseAgent 类（只保留 graph）
- ✅ 使用 LLMManager（线程安全）
- ✅ 修复重复函数
- ✅ 简化 retriever 管理
- ✅ 规范类型注解
"""

from __future__ import annotations

import os
import re
import logging
import asyncio  # ✅ 添加 asyncio 导入
from functools import wraps
from time import perf_counter
from typing import Any, Dict, List, Optional, Literal
from typing_extensions import TypedDict

from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, END
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage

from backend.app.agents.base_agent import (
    AgentItem,
    AgentRequest,
    get_llm_manager,
    create_structured_llm,  # LangChain 1.0: Structured Output
    call_structured_llm,    # LangChain 1.0: Structured Output
)
from backend.app.services.query_expansion import expand_query, MEDICAL_ARCHITECTURE_SYNONYMS
from backend.app.services.graph_retriever import AsyncGraphRetriever

logger = logging.getLogger(__name__)

DEFAULT_ANALYSIS_MODEL = os.getenv("NEO4J_AGENT_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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

class QueryAnalysisResult(BaseModel):
    """LLM 结构化输出：查询分析结果"""
    
    query_type: Literal["entity", "relation", "community", "mixed"] = Field(
        ...,
        description="本次查询的意图类型：实体、关系、社区或综合",
    )
    search_terms: List[str] = Field(
        default_factory=list,
        description="用于图谱检索的关键词、短语及其同义词/别名，按重要性排序",
    )
    reasoning: str = Field(
        default="",
        description="意图判断与关键词选择的理由",
    )


# ============================================================================
# 状态定义
# ============================================================================

class Neo4jState(TypedDict, total=False):
    """Neo4j智能体状态"""
    # 输入
    request: AgentRequest
    query: str
    filters: Dict[str, Any]
    
    # 查询分析
    query_type: str
    search_terms: List[str]
    
    # 检索参数
    depth: int
    k_edges: int
    top_k: int
    
    # 检索结果
    entity_results: List[AgentItem]
    relation_results: List[AgentItem]
    community_results: List[AgentItem]
    
    # 融合结果
    merged_items: List[AgentItem]
    items: List[AgentItem]  # ✅ 与父图兼容
    quality_score: float
    
    # 重试控制
    retry_count: int
    max_retries: int
    reflection: Dict[str, Any]
    
    # 输出
    diagnostics: Dict[str, Any]


# ============================================================================
# Retriever 管理（模块级单例）
# ============================================================================

_retriever: Optional[AsyncGraphRetriever] = None
_retriever_lock = asyncio.Lock()


def _init_retriever_sync() -> AsyncGraphRetriever:
    """同步初始化 AsyncGraphRetriever（由 asyncio.to_thread 调用）"""
    return AsyncGraphRetriever()


async def get_retriever() -> AsyncGraphRetriever:
    """
    获取或创建 AsyncGraphRetriever（异步版本，修复阻塞调用问题）

    2025-01-16: 使用asyncio.to_thread()包装同步初始化，
    避免LangGraph dev的阻塞调用检测（load_dotenv → os.getcwd）
    """
    global _retriever

    # 检查是否已初始化
    if _retriever is not None:
        return _retriever

    # 使用锁保护初始化过程
    async with _retriever_lock:
        # 双重检查
        if _retriever is not None:
            return _retriever

        # ✅ 使用 asyncio.to_thread() 在独立线程中初始化
        try:
            _retriever = await asyncio.to_thread(_init_retriever_sync)
            logger.info("[Neo4jAgent] AsyncGraphRetriever 初始化完成")
            return _retriever
        except Exception as e:
            logger.error(f"[Neo4jAgent] AsyncGraphRetriever 初始化失败: {e}")
            raise


# ============================================================================
# LLM 管理（使用 LLMManager）
# ============================================================================

def _init_analysis_llm():
    """初始化查询分析 LLM（同步版本）"""
    api_key = os.getenv("MEDIARCH_API_KEY")
    if not api_key:
        raise ValueError("缺少 MEDIARCH_API_KEY（neo4j_agent）")

    base_url = (os.getenv("OPENAI_BASE_URL") or "").rstrip("/") or None
    model_provider = os.getenv("OPENAI_MODEL_PROVIDER") or "openai"

    # 强制使用 OpenAI 兼容模式（支持第三方 API Gateway）
    base_model = init_chat_model(
        model=DEFAULT_ANALYSIS_MODEL,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_tokens=8000,
    )

    # [FIX 2025-12-09] 移除 with_structured_output()，改用手动解析
    # 原因：DeepSeek API 与 with_structured_output() 不兼容，导致 JSON 解析失败
    return base_model


async def get_analysis_llm():
    """
    获取查询分析 LLM（异步版本，修复阻塞调用问题）

    2025-01-16: 使用asyncio.to_thread()包装同步LLM初始化，
    彻底避免LangGraph dev的阻塞调用检测。
    """
    import asyncio

    manager = get_llm_manager()

    # 检查是否已缓存
    if "neo4j_analysis" in manager._instances:
        return manager._instances["neo4j_analysis"]

    # ✅ [FIX] 使用asyncio.to_thread()在独立线程中初始化LLM
    try:
        llm = await asyncio.to_thread(_init_analysis_llm)
        manager._instances["neo4j_analysis"] = llm
        return llm
    except Exception as e:
        logger.warning(f"[Neo4jAgent] LLM初始化失败: {e}")
        raise


# ============================================================================
# 辅助函数
# ============================================================================

def extract_keywords(query: str) -> List[str]:
    """从查询中提取关键词"""
    # 移除标点符号
    cleaned = re.sub(r'[,。、，？?！!;；:：""''()（）【】\[\]]', ' ', query)
    
    # 中文停用词
    stopwords = {
        '的', '了', '是', '在', '有', '和', '与', '或', '等', '及',
        '哪些', '什么', '如何', '怎么', '为什么', '介绍', '一下',
        '吗', '呢', '吧', '啊', '呀', '需要', '主要', '方面', '重点',
        '综合医院', '医院', '设计', '要点', '要求', '规范', '标准',
    }
    
    # 领域词表
    domain_terms = [
        '门诊部', '住院部', '急诊部', '急诊科', '急诊', '住院', '门诊',
        '儿科', '妇产科', '手术室', 'ICU', '重症监护', '检验科', '影像科',
        '放射科', '康复科', '血液科', '呼吸科', '神经内科', '神经外科',
        '肿瘤科', '消化科', '透析中心', '输液中心', '导管室', '麻醉科',
        '护理单元', '护士站', '医技科室', '放疗科', '体检中心', '产房',
        '新生儿科', '门诊大厅', '挂号收费', '药房', '检验', '放射', '影像',
    ]
    
    keywords: List[str] = []
    
    # 1) 匹配领域词
    for term in domain_terms:
        if term in query and term not in keywords:
            keywords.append(term)
    
    # 2) 去除常见后缀
    suffix_patterns = [
        '设计要点', '设计要求', '设计原则', '设计规范', '建设要点',
        '规划要点', '规划方案', '建设标准', '设计标准', '关键要点',
    ]
    base_query = query
    for suffix in suffix_patterns:
        if base_query.endswith(suffix):
            base_query = base_query[: -len(suffix)]
            break
    base_query = base_query.strip()
    if base_query and base_query not in keywords and base_query not in stopwords:
        keywords.append(base_query)
    
    # 3) 提取长度在2-6之间的中文短语
    tokens = re.findall(r'[\u4e00-\u9fa5]{2,6}', cleaned)
    for token in tokens:
        token = token.strip()
        if not token or token in stopwords or token in keywords:
            continue
        keywords.append(token)
    
    # 4) 回退
    if not keywords and len(query) >= 2:
        keywords = [query[:4]]
    
    return keywords[:5]


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


def merge_source_documents(*candidates: Any) -> List[str]:
    """
    规范化来源文档列表（用于跨资料追踪）

    Args:
        *candidates: 可能的来源字符串或列表

    Returns:
        去重且保留原顺序的来源列表
    """
    docs: List[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        if not candidate:
            continue

        values: List[str]
        if isinstance(candidate, str):
            values = [candidate]
        elif isinstance(candidate, (list, tuple, set)):
            values = [str(v) for v in candidate]
        else:
            continue

        for value in values:
            doc = (value or "").strip()
            if not doc or doc.lower() == "unknown":
                continue
            if doc not in seen:
                seen.add(doc)
                docs.append(doc)

    return docs


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def monitor_performance(node_name: str):
    """性能监控装饰器：记录节点耗时并写入 diagnostics"""
    def decorator(func):
        @wraps(func)
        async def wrapper(state: Neo4jState):
            start_time = perf_counter()
            try:
                result = await func(state)
            except Exception as e:
                took_ms = int((perf_counter() - start_time) * 1000)
                logger.error("[Neo4jAgent→%s] 失败，耗时: %sms, 错误: %s", node_name, took_ms, e)
                raise

            took_ms = int((perf_counter() - start_time) * 1000)
            logger.info("[Neo4jAgent→%s] 耗时: %sms", node_name, took_ms)

            if isinstance(result, dict):
                diagnostics: Dict[str, Any] = {}
                state_diag = state.get("diagnostics")
                if isinstance(state_diag, dict):
                    diagnostics.update(state_diag)
                result_diag = result.get("diagnostics")
                if isinstance(result_diag, dict):
                    diagnostics.update(result_diag)
                diagnostics[f"{node_name}_took_ms"] = took_ms
                result["diagnostics"] = diagnostics

            return result
        return wrapper
    return decorator


async def analyse_query_with_llm(query: str) -> Optional[QueryAnalysisResult]:
    """
    调用 LLM 获取查询意图与关键词（结构化输出优先 + 兼容兜底）

    - 优先尝试 LangChain Structured Output（与 Orchestrator 保持一致）
    - Structured Output 失败时，回退到手动解析（兼容 DeepSeek 等不支持的 API）
    """
    from backend.app.utils.llm_output_parser import parse_llm_output

    try:
        llm = await get_analysis_llm()
    except Exception as e:
        logger.warning(f"[Neo4jAgent] 无法获取 LLM: {e}，将使用启发式逻辑")
        return None

    system_prompt = (
        "你是一名医院建筑知识图谱的查询分析助手。"
        "请判断用户问题的意图类型（entity / relation / community / mixed），"
        "并输出适合图谱检索的 search_terms。"
        "\n\n**重要：你必须返回有效的 JSON 格式，不要包含任何其他文本或注释。**"
        "\n\n输出格式（必须是有效 JSON）："
        "\n{"
        "\n  \"query_type\": \"entity\","
        "\n  \"search_terms\": [\"手术室\", \"洁净手术部\", \"手术间\"],"
        "\n  \"reasoning\": \"用户询问手术室的设计要点，属于实体查询\""
        "\n}"
        "\n\n字段说明："
        "\n- query_type: 必须是 entity, relation, community, mixed 之一"
        "\n- search_terms: 关键词列表，至少3个"
        "\n- reasoning: 意图判断理由"
        "\n\n示例："
        "\n问题：手术室的设计要点？"
        "\n-> {\"query_type\": \"entity\", \"search_terms\": [\"手术室\", \"洁净手术部\", \"手术间\"], \"reasoning\": \"实体查询\"}"
        "\n问题：门诊部和住院部的关系？"
        "\n-> {\"query_type\": \"relation\", \"search_terms\": [\"门诊部\", \"住院部\", \"功能分区\"], \"reasoning\": \"关系查询\"}"
        "\n问题：急诊科有哪些子科室？"
        "\n-> {\"query_type\": \"community\", \"search_terms\": [\"急诊科\", \"急诊部\"], \"reasoning\": \"社区查询\"}"
    )

    # 1) Structured Output（带重试）
    from langchain_core.messages import HumanMessage
    max_attempts = 3
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result: QueryAnalysisResult = await call_structured_llm(
                llm=llm,
                pydantic_model=QueryAnalysisResult,
                messages=[
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=f"用户问题：{query}\n\n请直接返回 JSON，不要包含其他文本。"),
                ],
            )

            logger.info(
                f"[Neo4jAgent] LLM 结构化分析成功: "
                f"query_type={result.query_type}, "
                f"terms={result.search_terms[:5] if len(result.search_terms) > 5 else result.search_terms}"
            )
            return result
        except Exception as e:
            last_error = e
            if attempt < max_attempts and _is_transient_error(e):
                delay = min(2.0 * attempt, 6.0)
                logger.warning(
                    "[Neo4jAgent] 结构化输出瞬时错误，%s/%s 次重试后等待 %.1fs: %s",
                    attempt,
                    max_attempts,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
                continue
            logger.warning("[Neo4jAgent] 结构化输出失败，降级为手动解析: %s", e)
            break

    try:
        # [FIX 2025-12-09] LLM 不再绑定 with_structured_output()
        # 需要手动解析返回的内容
        raw_result = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"用户问题：{query}\n\n请直接返回 JSON，不要包含其他文本。")
        ])

        # 记录原始输出（用于调试）
        if hasattr(raw_result, 'content'):
            logger.debug(f"[Neo4jAgent] LLM 原始输出: {raw_result.content[:500]}...")
        else:
            logger.debug(f"[Neo4jAgent] LLM 原始输出: {str(raw_result)[:500]}...")

        # 使用通用解析器
        result = parse_llm_output(
            output=raw_result,
            pydantic_model=QueryAnalysisResult,
            fallback_parser=None
        )

        if result:
            logger.info(
                f"[Neo4jAgent] LLM 分析成功: "
                f"query_type={result.query_type}, "
                f"terms={result.search_terms[:5] if len(result.search_terms) > 5 else result.search_terms}"
            )
            return result
        else:
            logger.warning(f"[Neo4jAgent] LLM 输出解析失败，将使用启发式逻辑")
            return None

    except Exception as e:
        logger.error(f"[Neo4jAgent] LLM 查询分析异常: {e}，将使用启发式逻辑", exc_info=True)
        if last_error is not None:
            logger.debug("[Neo4jAgent] Structured Output 最后一次错误: %s", last_error)
        return None


def heuristic_query_analysis(query: str) -> Dict[str, Any]:
    """
    启发式查询分析（LLM 失败时的兜底）

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
            max_search_terms=30
        )

        search_terms = result.search_terms

        # 简单判断查询类型
        if any(word in query for word in ['关系', '连接', '之间', '如何', '和']):
            query_type = "relation"
        elif any(word in query for word in ['包含', '有哪些', '分类', '组成']):
            query_type = "community"
        elif any(word in query for word in ['综合', '整体', '全面']):
            query_type = "mixed"
        else:
            query_type = "entity"

        reasoning = (
            f"QueryExpansion: {len(result.keywords)}个关键词, "
            f"{len(result.synonyms)}个同义词, "
            f"类型={query_type}"
        )

        logger.info(
            f"[Neo4j→HeuristicAnalysis] "
            f"类型={query_type}, "
            f"关键词={result.keywords}, "
            f"总搜索词={len(search_terms)}"
        )

    except Exception as e:
        # 如果QueryExpansion失败，回退到基础方法
        logger.warning(f"[Neo4j→HeuristicAnalysis] QueryExpansion失败: {e}，使用基础方法")
        keywords = extract_keywords(query)

        # 简单判断
        if any(word in query for word in ['关系', '连接', '之间', '如何', '和']):
            query_type = "relation"
        elif any(word in query for word in ['包含', '有哪些', '分类', '组成']):
            query_type = "community"
        elif any(word in query for word in ['综合', '整体', '全面']):
            query_type = "mixed"
        else:
            query_type = "entity"

        search_terms = keywords
        reasoning = "启发式分析（基础模式）"

    return {
        "query_type": query_type,
        "search_terms": search_terms,
        "reasoning": reasoning,
    }


def calculate_quality(items: List[AgentItem], query: str) -> float:
    """计算检索质量分数"""
    if not items:
        return 0.0
    
    # 基础分数：结果数量
    count_score = min(len(items) / 5.0, 1.0) * 0.3
    
    # 分数分布：top-1 和平均分
    scores = [item.score or 0.0 for item in items]
    top1_score = max(scores) if scores else 0.0
    avg_score = sum(scores) / len(scores) if scores else 0.0
    
    score_quality = (top1_score * 0.4 + avg_score * 0.3)
    
    # 引用数量
    citation_count = sum(len(item.citations or []) for item in items)
    citation_score = min(citation_count / 3.0, 1.0) * 0.3
    
    total_score = count_score + score_quality + citation_score
    return min(total_score, 1.0)


# ============================================================================
# 节点函数
# ============================================================================

@monitor_performance("QueryAnalysis")
async def node_query_analysis(state: Neo4jState) -> Dict[str, Any]:
    """查询分析节点：理解用户意图和提取关键词"""
    query = state.get("query", "")
    
    logger.info(f"[Neo4jAgent→QueryAnalysis] 分析查询: {query}")
    
    # 尝试 LLM 分析
    llm_result = await analyse_query_with_llm(query)
    
    if llm_result:
        analysis = {
            "query_type": llm_result.query_type,
            "search_terms": deduplicate_terms(llm_result.search_terms),
        }
        logger.info(f"[Neo4jAgent→QueryAnalysis] LLM 分析: {analysis}")
    else:
        # 兜底：启发式分析
        analysis = heuristic_query_analysis(query)
        logger.info(f"[Neo4jAgent→QueryAnalysis] 启发式分析: {analysis}")
    
    return analysis


@monitor_performance("EntityMatch")
async def node_entity_match(state: Neo4jState) -> Dict[str, Any]:
    """
    实体匹配：精确匹配医院建筑实体

    [UPGRADED 2025-12-03]：并行优化
    - LLM智能扩展：串行 -> 并行（asyncio.gather）
    - 图查询：串行 -> 并行（asyncio.gather）
    - 预期提升：3-5倍速度

    [UPGRADED 2025-11-19 阶段2]：
    - 搜索词数量：8 → 12（更多关键词）
    - 每词候选数：50 → 100（更深度搜索）
    - 每词结果数：30 → 50（更多结果）
    - 添加同义词和变体搜索

    目标：确保覆盖《医院建筑设计指南》等所有资料源
    """
    search_terms = state.get("search_terms", [])
    top_k = state.get("top_k", 10)

    logger.info(f"[Neo4jAgent->EntityMatch] 检索实体: {search_terms}")

    retriever = await get_retriever()
    results = []

    # [UPGRADED] 8 -> 12个搜索词
    expanded_terms = search_terms[:12]

    # [UPGRADED 2025-12-03] 智能概念扩展改为并行执行
    try:
        from backend.app.services.intelligent_expansion import intelligent_concept_expansion

        # 对前3个重要词汇进行LLM驱动的智能扩展（并行）
        async def expand_term(term: str) -> list:
            try:
                concepts = await intelligent_concept_expansion(
                    term,
                    max_terms=8,
                    include_original=False
                )
                logger.info(f"[Neo4jAgent->EntityMatch] '{term}' 智能扩展: {concepts}")
                return concepts
            except Exception as e:
                logger.warning(f"[Neo4jAgent->EntityMatch] '{term}' 扩展失败: {e}")
                return []

        # 并行执行LLM扩展（3个词同时扩展）
        expansion_tasks = [expand_term(term) for term in search_terms[:3]]
        expansion_results = await asyncio.gather(*expansion_tasks, return_exceptions=True)

        for result in expansion_results:
            if isinstance(result, list):
                expanded_terms.extend(result)

    except Exception as e:
        logger.warning(f"[Neo4jAgent->EntityMatch] 智能扩展失败: {e}，回退到基础同义词")

        # [FALLBACK] 如果智能扩展失败，回退到基础同义词映射
        for term in search_terms[:4]:
            synonyms_map = {
                "寻路": ["导向", "导视", "指引", "路径", "标识"],
                "标牌": ["标识", "导向牌", "指示牌", "路牌"],
                "导向": ["导视", "寻路", "指引", "标识"],
                "门诊": ["门诊部", "门诊科", "门诊区"],
                "医技": ["医技科室", "辅助科室", "医技部门"],
                "空间": ["区域", "房间", "场所", "布局"],
                "护理单元": ["病区", "护士站", "病房区", "护理区"],
                "手术室": ["手术间", "洁净手术部", "手术区"],
            }

            for key, synonyms in synonyms_map.items():
                if key in term:
                    expanded_terms.extend(synonyms[:2])

    # 去重并限制总数
    expanded_terms = list(dict.fromkeys(expanded_terms))[:15]  # 最多15个搜索词

    logger.info(f"[Neo4jAgent->EntityMatch] 扩展后搜索词: {expanded_terms}")

    # [UPGRADED 2025-12-03] 图查询改为并行执行
    async def search_term_entities(term: str) -> list:
        """单个词的实体搜索"""
        try:
            entities = await retriever.search_nodes(
                query=term,
                k=100  # 100个候选
            )

            term_results = []
            for entity in entities[:50]:  # 50个结果
                source_docs = merge_source_documents(
                    entity.get("source_document"),
                    entity.get("source_documents"),
                )
                primary_source = source_docs[0] if source_docs else (entity.get("source_document") or "unknown")

                item = AgentItem(
                    entity_id=entity.get("slug", ""),
                    name=entity.get("name", ""),
                    label=entity.get("label", ""),
                    score=entity.get("score", 0.8),
                    snippet=f"[{primary_source}] {entity.get('label', '')} - {entity.get('name', '')}",
                    attrs={
                        "slug": entity.get("slug", ""),
                        "source_document": primary_source if primary_source else "unknown",
                        "source_documents": source_docs,
                        "search_term": term,
                    },
                    source="neo4j_agent",
                )
                term_results.append(item)
            return term_results
        except Exception as e:
            logger.error(f"[Neo4jAgent->EntityMatch] 检索失败 term='{term}': {e}")
            return []

    # 并行执行所有搜索词的图查询
    search_tasks = [search_term_entities(term) for term in expanded_terms]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    for result in search_results:
        if isinstance(result, list):
            results.extend(result)

    # 🔧 [FIX] 去重逻辑：保持跨资料多样性
    # 原逻辑：基于 entity_id 或 label:name 去重，会丢失不同来源的同名实体
    # 新逻辑：去重key包含source_document，保留不同来源的条目
    deduped: Dict[str, AgentItem] = {}
    for item in results:
        primary_source = item.attrs.get("source_document", "unknown")
        base_key = item.entity_id or f"{item.label}:{item.name}"

        # 🔧 [FIX] 新的去重key：entity_id + source_document
        key = f"{base_key}#{primary_source}"

        if key not in deduped:
            deduped[key] = item
        else:
            # 如果有重复，保留score更高的那个
            if (item.score or 0.0) > (deduped[key].score or 0.0):
                deduped[key] = item

    deduped_results = list(deduped.values())

    # 🔧 [FIX] 进一步确保跨资料平衡：如果某个资料源结果过多，进行平衡
    source_coverage: Dict[str, List[AgentItem]] = {}
    for item in deduped_results:
        doc = item.attrs.get("source_document", "unknown")
        if doc not in source_coverage:
            source_coverage[doc] = []
        source_coverage[doc].append(item)

    # 如果有资料源结果数量超过其他的3倍以上，进行平衡
    max_per_source = max(len(items) // 2, 10) if len(source_coverage) > 1 else 100
    balanced_results = []
    for doc, items in source_coverage.items():
        if len(items) > max_per_source:
            # 按score排序，保留最佳结果
            items.sort(key=lambda x: x.score or 0.0, reverse=True)
            balanced_results.extend(items[:max_per_source])
            logger.info(f"[Neo4jAgent→EntityMatch] {doc} 结果过多({len(items)})，平衡至{max_per_source}个")
        else:
            balanced_results.extend(items)

    final_results = balanced_results

    # 统计最终的来源分布
    final_source_coverage: Dict[str, int] = {}
    for item in final_results:
        doc = item.attrs.get("source_document", "unknown")
        final_source_coverage[doc] = final_source_coverage.get(doc, 0) + 1

    logger.info(
        "[Neo4jAgent→EntityMatch] 找到 %s 个实体（去重后 %s 个，平衡后 %s 个），来源分布: %s",
        len(results),
        len(deduped_results),
        len(final_results),
        {k: v for k, v in sorted(final_source_coverage.items(), key=lambda x: x[1], reverse=True) if k},
    )

    return {"entity_results": final_results}


@monitor_performance("RelationReasoning")
async def node_relation_reasoning(state: Neo4jState) -> Dict[str, Any]:
    """
    关系推理：查找实体间的关系路径

    [UPGRADED 2025-11-19 阶段2]：
    - 起始节点数：8 → 15（更多实体）
    - 扩展深度：4 → 5（更深层关系）
    - 边数限制：500 → 1000（更多关系）
    - 结果数量：50 → 150（更全面的关系网络）

    目标：发现《医院建筑设计指南》中的设计规范关系
    """
    search_terms = state.get("search_terms", [])
    depth = state.get("depth", 3)  # ✅ [2025-12-18] 优化：从5降低到3
    k_edges = state.get("k_edges", 200)  # ✅ [2025-12-18] 优化：从1000降低到200

    logger.info(f"[Neo4jAgent→RelationReasoning] 推理关系: {search_terms}, depth={depth}, k_edges={k_edges}")

    retriever = await get_retriever()
    results = []

    # 首选：使用实体匹配的真实节点作为起点
    entity_results = state.get("entity_results", []) or []
    seed_terms: List[str] = []
    for item in entity_results[:20]:
        if item.entity_id:
            seed_terms.append(item.entity_id)
        if item.name:
            seed_terms.append(item.name)

    if not seed_terms:
        seed_terms = deduplicate_terms(search_terms)[:15]

    if seed_terms:
        try:
            relations = await retriever.expand_neighborhood(
                slugs=seed_terms,
                depth=depth,
                k_edges=k_edges,
            )

            # ✅ [UPGRADED] 50 → 150个关系
            for rel in relations[:150]:
                source_docs = merge_source_documents(
                    rel.get("a_source_document"),
                    rel.get("a_source_documents"),
                    rel.get("b_source_document"),
                    rel.get("b_source_documents"),
                )
                source_doc = source_docs[0] if source_docs else (
                    rel.get("a_source_document") or rel.get("b_source_document") or "unknown"
                )

                item = AgentItem(
                    entity_id=f"rel_{rel.get('a_slug', '')}__{rel.get('b_slug', '')}",
                    name=f"{rel.get('a_name', '')} → {rel.get('b_name', '')}",
                    label=rel.get("rel_type", "RELATION"),
                    score=0.8,
                    snippet=f"[{source_doc}] {rel.get('a_name', '')} --{rel.get('rel_type', '')}-> {rel.get('b_name', '')}",
                    attrs={
                        "source_node": rel.get("a_slug", ""),
                        "target_node": rel.get("b_slug", ""),
                        "relation_type": rel.get("rel_type", ""),
                        "source_document": source_doc,
                        "source_documents": source_docs,
                    },
                    source="neo4j_agent",
                )
                results.append(item)
        except Exception as e:
            logger.error(f"[Neo4jAgent→RelationReasoning] 扩展失败: {e}")

    logger.info(f"[Neo4jAgent→RelationReasoning] 找到 {len(results)} 条关系")

    return {"relation_results": results}


@monitor_performance("CommunityFilter")
async def node_community_filter(state: Neo4jState) -> Dict[str, Any]:
    """
    社区过滤：查找子系统和功能分区

    [UPGRADED 2025-11-19 阶段2]：
    - 搜索词数：5 → 10（更多概念）
    - 候选节点数：30 → 80（更深度搜索）
    - 社区规模：2 → 3（更严格的社区要求）

    目标：发现医院建筑设计中的功能分区和系统性知识
    """
    search_terms = state.get("search_terms", [])

    logger.info(f"[Neo4jAgent→CommunityFilter] 检索社区: {search_terms}")

    retriever = await get_retriever()
    results = []

    # ✅ [UPGRADED] 5 → 10个搜索词
    for term in search_terms[:10]:
        try:
            # ✅ [UPGRADED] 30 → 80个候选节点
            nodes = await retriever.search_nodes(
                query=term,
                k=80,
            )

            # 按标签分组，作为简单的社区检测
            label_groups = {}
            for node in nodes:
                label = node.get("label", "Unknown")
                if label not in label_groups:
                    label_groups[label] = []
                node_sources = merge_source_documents(
                    node.get("source_document"),
                    node.get("source_documents"),
                )
                node["_source_documents"] = node_sources
                label_groups[label].append(node)

            # 将每个标签组作为一个社区
            for label, group_nodes in label_groups.items():
                if len(group_nodes) >= 3:  # ✅ [UPGRADED] 至少3个节点才算社区（从2改为3）
                    community_names = [n.get("name", "") for n in group_nodes[:8]]  # 社区成员从5个增加到8个
                    community_sources = merge_source_documents(
                        *[n.get("_source_documents") for n in group_nodes]
                    )
                    source_label = " / ".join(community_sources[:2]) if community_sources else "multiple"

                    item = AgentItem(
                        entity_id=f"community_{label}_{term}",
                        name=f"{label}社区",
                        label="Community",
                        score=0.8,
                        snippet=f"[{source_label}] {label}类型节点社区，包含: {', '.join(community_names)}",
                        attrs={
                            "community_type": label,
                            "members": community_names,
                            "size": len(group_nodes),
                            "source_document": community_sources[0] if community_sources else "multiple",
                            "source_documents": community_sources,
                            "search_term": term,  # ✅ [NEW] 记录搜索词
                        },
                        source="neo4j_agent",
                    )
                    results.append(item)
        except Exception as e:
            logger.error(f"[Neo4jAgent→CommunityFilter] 检索失败: {e}")

    logger.info(f"[Neo4jAgent→CommunityFilter] 找到 {len(results)} 个社区")

    return {"community_results": results}


@monitor_performance("MergeResults")
async def node_merge_results(state: Neo4jState) -> Dict[str, Any]:
    """
    融合结果：合并多个检索结果并去重

    阶段1: 增加最终结果数量（7个 → 25个）
    阶段2: 添加Round-Robin来源交替选择，保证跨资料多样性

    核心改进：
    - 按source_document分组
    - 使用Round-Robin算法交替选择不同来源的结果
    - 保证用户能看到来自多个资料的答案
    """
    query_type = state.get("query_type", "entity")
    query = state.get("query", "")
    search_terms = state.get("search_terms", [])

    # ✅ [UPGRADED 阶段2] 大幅增加候选数量，提升跨资料覆盖
    # 根据查询类型选择主要结果
    if query_type == "entity":
        primary = state.get("entity_results", [])[:200]  # 50 → 200
        secondary = state.get("relation_results", [])[:100] + state.get("community_results", [])[:100]  # 30 → 200
    elif query_type == "relation":
        primary = state.get("relation_results", [])[:200]  # 50 → 200
        secondary = state.get("entity_results", [])[:100] + state.get("community_results", [])[:100]  # 30 → 200
    elif query_type == "community":
        primary = state.get("community_results", [])[:150]  # 30 → 150
        secondary = state.get("entity_results", [])[:100] + state.get("relation_results", [])[:100]  # 30 → 200
    else:  # mixed
        primary = (
            state.get("entity_results", [])[:100] +  # 30 → 100
            state.get("relation_results", [])[:100] +  # 30 → 100
            state.get("community_results", [])[:100]  # 15 → 100
        )
        secondary = []

    # 先规范化来源信息，便于后续统计
    source_unknown = 0
    for item in primary + secondary:
        docs = merge_source_documents(
            item.attrs.get("source_document"),
            item.attrs.get("source_documents"),
        )
        if docs:
            item.attrs["source_document"] = docs[0]
            item.attrs["source_documents"] = docs
        else:
            if not item.attrs.get("source_document"):
                item.attrs["source_document"] = "unknown"
            item.attrs.setdefault("source_documents", [])
            source_unknown += 1

    # ✅ [NEW 阶段2] 按来源分组（Round-Robin来源交替选择）
    from collections import defaultdict
    by_source = defaultdict(list)

    for item in primary + secondary:
        source_doc = item.attrs.get("source_document", "unknown")
        by_source[source_doc].append(item)

    # ✅ [NEW 阶段2] Round-Robin交替选择不同来源
    merged = []
    max_rounds = 10  # 每个来源最多取10个

    for round_idx in range(max_rounds):
        # 按来源名称排序，确保稳定性（过滤None值）
        for source_doc in sorted(k for k in by_source.keys() if k is not None):
            items = by_source[source_doc]
            if round_idx < len(items):
                item = items[round_idx]

                # 去重检查
                if item.entity_id and item.entity_id in {m.entity_id for m in merged if m.entity_id}:
                    continue

                merged.append(item)

        # ✅ [UPGRADED] 如果已经收集够了50个，提前退出
        if len(merged) >= 50:
            break

    # 限制最终数量
    merged = merged[:50]  # 25 → 50，大幅提升输出结果数量

    # ✅ [NEW 阶段2] 统计来源分布（用于诊断）
    source_stats = {}
    for item in merged:
        source = item.attrs.get("source_document", "unknown")
        source_stats[source] = source_stats.get(source, 0) + 1

    # 按分数排序
    merged.sort(key=lambda x: x.score or 0.0, reverse=True)

    # 计算质量分数
    quality_score = calculate_quality(merged, query)

    # ✅ [NEW 阶段2] 输出来源分布日志
    logger.info(
        f"[Neo4jAgent→MergeResults] 融合 {len(merged)} 项, 质量={quality_score:.2f}, "
        f"来源多样性={len(source_stats)}个资料"
    )
    logger.info(f"[Neo4jAgent→MergeResults] 来源分布: {source_stats}")

    # ✅ 确保所有 item 都有 source
    for item in merged:
        if not item.source:
            item.source = "neo4j_agent"

    # ✅ [NEW] 构建知识图谱查询路径（用于Result Synthesizer显示）
    query_path = {
        "original_query": query,
        "query_type": query_type,
        "search_terms": search_terms[:5],  # 保留前5个搜索词
        "entity_count": len(state.get("entity_results", [])),
        "relation_count": len(state.get("relation_results", [])),
        "community_count": len(state.get("community_results", [])),
        "expanded_entities": [],
        "expanded_relations": [],
        "knowledge_coverage": []
    }

    # 提取扩展的知识点
    for item in merged[:8]:  # 只分析前8个最相关的结果
        if item.label and item.name:
            query_path["expanded_entities"].append({
                "name": item.name,
                "type": item.label,
                "score": item.score
            })

        # 提取关系信息（如果有edges）
        if item.edges:
            for edge in item.edges[:3]:  # 每个实体最多3条关系
                rel_type = edge.get("type", "未知关系")
                target = edge.get("target", "")
                if target:
                    query_path["expanded_relations"].append({
                        "source": item.name,
                        "relation": rel_type,
                        "target": target
                    })

    # 统计知识覆盖领域
    entity_types = {}
    for item in merged:
        if item.label:
            entity_types[item.label] = entity_types.get(item.label, 0) + 1

    query_path["knowledge_coverage"] = [
        {"domain": k, "count": v}
        for k, v in sorted(entity_types.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "merged_items": merged,
        "items": merged,  # ✅ 与父图兼容
        "quality_score": quality_score,
        "diagnostics": {
            "query_path": query_path,  # ✅ 供Result Synthesizer使用
            "query_type": query_type,
            "result_count": len(merged),
            "source_diversity": len(source_stats),  # ✅ [NEW 阶段2] 来源多样性
            "source_distribution": source_stats,  # ✅ [NEW 阶段2] 来源分布
            "unknown_sources": source_unknown,
        }
    }


@monitor_performance("Reflection")
async def node_reflection(state: Neo4jState) -> Dict[str, Any]:
    """反思：评估检索质量，决定是否重试"""
    quality_score = state.get("quality_score", 0.0)
    merged_items = state.get("merged_items", [])
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    # [FIX 2026-01-27] 默认值与 init_params 保持一致
    DEFAULT_DEPTH = _coerce_int(os.getenv("NEO4J_DEPTH", "3"), 3)
    DEFAULT_K_EDGES = _coerce_int(os.getenv("NEO4J_K_EDGES", "200"), 200)

    current_depth = state.get("depth", DEFAULT_DEPTH)
    current_k_edges = state.get("k_edges", DEFAULT_K_EDGES)

    reflection = {
        "quality": "good" if quality_score >= 0.4 else "low",
        "score": quality_score,
        "items_count": len(merged_items),
    }

    # 决定是否重试
    if quality_score < 0.4 and retry_count < max_retries and len(merged_items) == 0:
        reflection["action"] = "retry"
        reflection["reason"] = f"质量分数 {quality_score:.2f} 低于阈值"
        logger.warning(f"[Neo4jAgent->Reflection] 质量不足，准备重试 (retry={retry_count})")
    else:
        reflection["action"] = "finish"
        logger.info(f"[Neo4jAgent->Reflection] 质量合格，完成检索")

    return {
        "reflection": reflection,
        "retry_count": retry_count + (1 if reflection["action"] == "retry" else 0),
        "depth": current_depth + 1 if reflection["action"] == "retry" else current_depth,
        "k_edges": current_k_edges + 100 if reflection["action"] == "retry" else current_k_edges,
    }


@monitor_performance("AddCitations")
async def node_add_citations(state: Neo4jState) -> Dict[str, Any]:
    """
    添加规范引用（优化版本 - 2025-12-09, 2026-01-27修复）

    改进：
    - 使用统一的 citation_builder 工具函数
    - 确保所有 citations 包含必填字段
    - 提高代码可维护性
    - [FIX 2026-01-27] 将循环内的重复查询移到循环外，避免性能浪费
    """
    from backend.app.utils.citation_builder import build_kg_citation, build_spec_citation

    merged_items = state.get("merged_items", [])
    query = state.get("query", "")

    retriever = await get_retriever()

    # [FIX 2026-01-27] 预先查询规范文档（只查询一次，避免循环内重复查询）
    fallback_specs = None
    items_needing_fallback = []

    for item in merged_items:
        source_docs = merge_source_documents(
            item.attrs.get("source_document"),
            item.attrs.get("source_documents"),
        )

        if source_docs:
            # 使用统一的 build_kg_citation 函数
            item.citations = [
                build_kg_citation(
                    source=doc,
                    entity_label=item.label or "Entity",
                    entity_name=item.name or "",
                    snippet=(item.snippet or f"{item.label} - {item.name}")[:200],
                    entity_id=item.entity_id,
                    search_term=item.attrs.get("search_term"),
                    id=f"{item.entity_id or 'entity'}_{idx}",
                )
                for idx, doc in enumerate(source_docs[:3])
            ]
        elif not item.citations:
            # 记录需要兜底引用的 item
            items_needing_fallback.append(item)

    # [FIX 2026-01-27] 只有在需要时才查询规范文档（一次查询，多次使用）
    if items_needing_fallback:
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                fallback_specs = await retriever.search_nodes(
                    query=f"{query} 规范 标准",
                    k=3,
                )
                break
            except Exception as e:
                logger.warning(
                    "[Neo4jAgent->AddCitations] 获取规范引用失败（%s/%s）：%s",
                    attempt,
                    max_attempts,
                    e,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.5 * attempt)
                fallback_specs = []

        # 为所有需要兜底的 item 添加相同的规范引用
        if fallback_specs:
            fallback_citations = [
                build_spec_citation(
                    source=spec.get("name", "规范"),
                    spec_label=spec.get("label", "DesignSpec"),
                    spec_name=spec.get("name", ""),
                    snippet=f"{spec.get('label', '')} - {spec.get('name', '')}",
                    slug=spec.get("slug", ""),
                    id=idx,
                )
                for idx, spec in enumerate(fallback_specs)
            ]
            for item in items_needing_fallback:
                # 避免共享同一 list/dict，防止后续原地修改互相影响
                item.citations = [dict(citation) for citation in fallback_citations]

    return {"items": merged_items}


def route_after_reflection(state: Neo4jState) -> str:
    """路由：重试或结束"""
    action = state.get("reflection", {}).get("action", "finish")
    return "query_analysis" if action == "retry" else "add_citations"


# ============================================================================
# 构建图
# ============================================================================

def build_neo4j_graph():
    """构建 Neo4j Agent 图"""
    builder = StateGraph(Neo4jState)

    # ✅ [UPGRADED 2025-01-17] 初始化节点 - 增强检索深度
    @monitor_performance("InitParams")
    async def init_params(state: Neo4jState) -> Dict[str, Any]:
        request = state.get("request")
        metadata = request.metadata if request else {}

        env_max_retries = _coerce_int(os.getenv("NEO4J_MAX_RETRIES", "1"), 1)
        env_depth = _coerce_int(os.getenv("NEO4J_DEPTH", "3"), 3)
        env_k_edges = _coerce_int(os.getenv("NEO4J_K_EDGES", "200"), 200)

        max_retries = _coerce_int(metadata.get("neo4j_max_retries"), env_max_retries)
        depth = _coerce_int(metadata.get("neo4j_depth"), env_depth)
        k_edges = _coerce_int(metadata.get("neo4j_k_edges"), env_k_edges)
        default_top_k = 10
        if request:
            default_top_k = request.top_k
        elif "top_k" in state and state["top_k"] is not None:
            default_top_k = state["top_k"]

        top_k = _coerce_int(metadata.get("neo4j_top_k"), default_top_k)

        return {
            "max_retries": max_retries,
            "retry_count": 0,
            "depth": depth,  # ✅ [2025-12-18] 优化：从4降低到3，减少遍历开销
            "k_edges": k_edges,  # ✅ [2025-12-18] 优化：从500降低到200，提升检索效率
            "top_k": top_k,
        }

    # 添加所有节点
    builder.add_node("init_params", init_params)
    builder.add_node("query_analysis", node_query_analysis)
    builder.add_node("entity_match", node_entity_match)
    builder.add_node("relation_reasoning", node_relation_reasoning)
    builder.add_node("community_filter", node_community_filter)
    builder.add_node("merge_results", node_merge_results)
    builder.add_node("reflection", node_reflection)
    builder.add_node("add_citations", node_add_citations)

    # 入口
    builder.set_entry_point("init_params")
    builder.add_edge("init_params", "query_analysis")

    # ✅ [CRITICAL FIX] 所有查询类型都执行完整的3个检索节点（确保跨资料检索）
    # 固定流程：query_analysis → entity_match → relation_reasoning → community_filter → merge_results
    # 这样确保每个查询都能获得最全面的知识图谱结果
    builder.add_edge("query_analysis", "entity_match")
    builder.add_edge("entity_match", "relation_reasoning")
    builder.add_edge("relation_reasoning", "community_filter")
    builder.add_edge("community_filter", "merge_results")

    # 融合后反思
    builder.add_edge("merge_results", "reflection")

    # 反思后条件路由
    builder.add_conditional_edges(
        "reflection",
        route_after_reflection,
        {
            "query_analysis": "query_analysis",  # 重试
            "add_citations": "add_citations",  # 完成
        }
    )

    # 添加引用后结束
    builder.add_edge("add_citations", END)

    logger.info("[Neo4jAgent] 图构建完成（已应用2025-01-17优化：阶段1+2）")

    return builder.compile()


# ============================================================================
# 导出图
# ============================================================================

graph = build_neo4j_graph()

logger.info("[Neo4jAgent] 图已导出（纯 StateGraph 模式）")
