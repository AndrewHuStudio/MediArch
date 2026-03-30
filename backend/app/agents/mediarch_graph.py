"""
MediArch Graph - 真正并行检索架构版本

这是 MediArch 系统的主 MediArch Graph，负责协调所有 worker agents。

核心优化：
- ✅ 使用 base_agent 的标准 Reducer 和类型（无重复代码）
- ✅ 使用 create_worker_adapter 统一包装 Worker
- ✅ 添加 worker_responses 字段（Synthesizer 可获取完整信息）
- ✅ 使用 LLMManager 管理 LLM（线程安全）
- ✅ 对话历史管理（支持多轮对话）
- ✅ 持久化存储（预留接口，支持多用户）

2025-11-25 重大升级：真正并行检索架构
- ✅ Neo4j 和 Milvus 真正并行启动
- ✅ Knowledge Fusion 节点合并两边优势
- ✅ 生成统一检索线索供 MongoDB 精确定位
- ✅ 输出完整 graph_data 供前端知识图谱可视化
- ✅ 支持 citations 精确位置用于 PDF 高亮
- ✅ 检索结果缓存机制

2025-12-03 简化：
- ✅ 移除 Human-in-the-Loop 机制（过于机械，效果不佳）
- ✅ 移除 FEEDBACK_CLASSIFIER 相关配置
"""

from __future__ import annotations

import json
import logging
import os
import time
from operator import add
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict,Annotated

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage,AIMessage,AnyMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

# ============================================================================
# 导入 base_agent 的标准组件（避免重复定义）
# ============================================================================
from backend.app.agents.base_agent import (
    AgentItem,
    AgentRequest,
    AgentResponse,
    # 标准 Reducer 和类型
    RequestAnnotated,
    ItemsAnnotated,
    DiagnosticsAnnotated,
    WorkerResponsesAnnotated,
    keep_latest_request,
    add_items_with_dedup,
    merge_diagnostics,
    # Worker Adapter
    create_worker_adapter,
    # LLM 管理
    get_llm_manager,
)
from backend.app.agents.persistence import (
    POSTGRES_CHECKPOINTER_AVAILABLE,
    POSTGRES_STORE_AVAILABLE,
    SQLITE_BACKEND_AVAILABLE,
    create_checkpointer_from_runtime,
    create_store_from_runtime,
)
from backend.app.agents.postgres_deployment_policy import get_shared_postgres_uri
from backend.app.agents.routing_policy import select_workers_for_execution
from backend.app.agents.runtime_policy import (
    build_checkpointer_runtime_diagnostics,
    build_phase1_runtime_diagnostics,
    build_store_runtime_diagnostics,
    resolve_checkpointer_runtime_status,
    resolve_phase1_runtime_mode,
    resolve_store_runtime_status,
)

# 导入 Orchestrator
from backend.app.agents.orchestrator_agent import orchestrator_logic_graph

# 2025-11-25: 导入 Knowledge Fusion 和缓存模块
from backend.app.agents.knowledge_fusion import (
    fuse_retrieval_results,
    KnowledgeFusionResult,
)
from backend.app.agents.retrieval_cache import get_retrieval_cache


logger = logging.getLogger("mediarch_graph")


# ============================================================================
# 环境配置
# ============================================================================
DEFAULT_TIMEOUT_MS = int(os.getenv("AGENT_TIMEOUT_MS", "3000"))
DEFAULT_TOP_K = int(os.getenv("ORCH_TOPK_DEFAULT", "20"))  # [FIX 2026-01-14] 从8增加到20
DEFAULT_WORKER_PRIORITY: List[str] = [
    "neo4j_agent",
    "milvus_agent",
    "mongodb_agent",
    "online_search_agent",
]
# Phase1 retrieval strategy:
# - configured mode comes from env for observability;
# - effective mode is currently pinned to `parallel` so experiments and paper claims stay aligned.
PHASE1_RUNTIME_MODE = resolve_phase1_runtime_mode(os.getenv("PHASE1_RETRIEVAL_MODE"))
PHASE1_RETRIEVAL_MODE = PHASE1_RUNTIME_MODE["effective_mode"]

if PHASE1_RUNTIME_MODE["is_forced"]:
    logger.info(
        "[MediArchGraph] PHASE1_RETRIEVAL_MODE=%s, but effective experiment mode is fixed to %s",
        PHASE1_RUNTIME_MODE["configured_mode"],
        PHASE1_RUNTIME_MODE["effective_mode"],
    )

# ============================================================================
# [NEW] 2025-01-16: Checkpointer配置
# ============================================================================
CHECKPOINT_BACKEND = os.getenv("CHECKPOINT_BACKEND", "sqlite")  # "sqlite", "postgres", "memory"
SQLITE_CHECKPOINT_PATH = os.getenv("SQLITE_CHECKPOINT_PATH", ".langgraph_api/checkpoints.db")
POSTGRES_CHECKPOINT_URI = os.getenv(
    "POSTGRES_CHECKPOINT_URI",
    get_shared_postgres_uri(),
)
STORE_BACKEND = os.getenv("STORE_BACKEND", CHECKPOINT_BACKEND)
SQLITE_STORE_PATH = os.getenv("SQLITE_STORE_PATH", ".langgraph_api/store.db")
POSTGRES_STORE_URI = os.getenv("POSTGRES_STORE_URI", get_shared_postgres_uri())

_IS_LANGGRAPH_API = (
    os.getenv("LANGGRAPH_API_VERSION") is not None or
    os.getenv("LANGGRAPH_RUNTIME") == "api"
)
CHECKPOINTER_RUNTIME_STATUS = resolve_checkpointer_runtime_status(
    CHECKPOINT_BACKEND,
    is_langgraph_api=_IS_LANGGRAPH_API,
    sqlite_available=SQLITE_BACKEND_AVAILABLE,
    postgres_available=POSTGRES_CHECKPOINTER_AVAILABLE,
)
STORE_RUNTIME_STATUS = resolve_store_runtime_status(
    STORE_BACKEND,
    is_langgraph_api=_IS_LANGGRAPH_API,
    sqlite_available=SQLITE_BACKEND_AVAILABLE,
    postgres_available=POSTGRES_STORE_AVAILABLE,
)


# ============================================================================
# 长期记忆 Store（全局实例，线程安全）
# ============================================================================
_memory_store: Optional[Any] = None


def get_memory_store():
    """获取全局记忆 store（线程安全）"""
    global _memory_store
    if _memory_store is None:
        _memory_store = create_store_from_runtime(
            STORE_RUNTIME_STATUS,
            sqlite_path=SQLITE_STORE_PATH,
            postgres_uri=POSTGRES_STORE_URI,
        )
        logger.info(
            "[Memory] %s 已初始化（configured=%s, effective=%s, fallback_reason=%s）",
            type(_memory_store).__name__,
            STORE_RUNTIME_STATUS["configured_backend"],
            STORE_RUNTIME_STATUS["effective_backend"],
            STORE_RUNTIME_STATUS["fallback_reason"],
        )
    return _memory_store


# ============================================================================
# 状态定义（使用标准类型注解）
# ============================================================================

class MediArchGraphState(TypedDict, total=False):

    # ✅ 新增：用于 Studio Chat 的消息通道1.
    messages: Annotated[list[AnyMessage], add_messages]

    # ========== 标准字段 ==========
    request: RequestAnnotated  # 自动使用 keep_latest_request
    query: str
    original_query: str  # 原始用户问题（避免 Orchestrator 改写丢约束）
    items: ItemsAnnotated  # 自动去重合并（add_items_with_dedup）
    diagnostics: DiagnosticsAnnotated  # 自动合并（merge_diagnostics）

    # ========== Worker 响应（新增）==========
    worker_responses: WorkerResponsesAnnotated  # 完整的 Worker 响应（追加）

    # ========== Orchestrator 输出 ==========
    is_hospital_related: bool
    agents_to_call: List[str]
    rewritten_query: str

    # ========== 知识扩展上下文 ==========
    neo4j_expansion: Dict[str, Any]  # 来自Neo4j或启发式的知识扩展结果
    subtopics: List[str]

    # ========== 2025-11-25 新增：并行检索架构支持 ==========
    # 检索阶段标记
    parallel_retrieval_phase: str  # "phase1_parallel" | "phase2_fusion" | "phase3_mongodb"

    # 分离的 Worker 结果（用于 Fusion）
    neo4j_items: List[AgentItem]  # Neo4j 检索结果
    milvus_items: List[AgentItem]  # Milvus 检索结果

    # Knowledge Fusion 输出
    unified_hints: Dict[str, Any]  # 统一检索线索
    answer_graph_data: Dict[str, Any]  # 答案图谱数据（供前端可视化）

    # 缓存控制
    cache_hit: bool  # 是否命中缓存

    # ========== 严格资料范围（跨文档回归）==========
    strict_cross_doc_request: bool  # 用户要求严格交叉验证/仅基于指定资料
    strict_doc_ids: List[str]  # 资料范围（doc_id）
    strict_source_documents: List[str]  # 资料范围（title）

    # ========== Worker 调度 ==========
    available_workers: List[str]
    scheduled_workers: List[str]
    expected_workers_count: int
    completed_workers: Annotated[List[str], add]  # 使用 add reducer 支持并发追加
    active_workers: List[str]

    # ========== Synthesizer 输出 ==========
    final_answer: str
    recommended_questions: List[str]
    quality_score: float
    # ✅ 严格交叉验证：用于 API 对齐 [n] 引用
    strict_cross_doc: bool
    strict_citations_candidate_count: int
    final_citations: List[Dict[str, Any]]

    # ========== Human-in-the-Loop（新增）==========
    waiting_for_feedback: bool  # 是否等待用户反馈
    user_feedback_raw: str  # 用户的原始反馈
    user_feedback_type: str  # LLM 分类后的类型："satisfied" | "unsatisfied" | "new_question"
    feedback_round: int  # 反馈轮次：0=首次，1=第一次反馈，2=第二次反馈

    # ========== 对话历史（新增）==========
    conversation_history: List[Dict[str, str]]  # [{"role": "user", "content": "..."}, ...]
    user_id: str  # 用户ID（用于多用户支持）
    session_id: str  # 会话ID（用于区分不同会话）


# ============================================================================
# 长期记忆辅助函数
# ============================================================================

def get_user_preferences(user_id: str) -> Dict[str, Any]:
    """读取用户偏好（关注科室、设计风格等）"""
    store = get_memory_store()
    item = store.get(("users",), user_id)
    
    if item and item.value:
        logger.info(f"[Memory] 读取用户 {user_id} 的偏好: {item.value}")
        return dict(item.value)
    else:
        logger.info(f"[Memory] 用户 {user_id} 无历史偏好")
        return {}


def save_user_preferences(user_id: str, preferences: Dict[str, Any]) -> None:
    """保存用户偏好"""
    store = get_memory_store()
    store.put(("users",), user_id, preferences)
    logger.info(f"[Memory] 已保存用户 {user_id} 的偏好: {preferences}")


def get_conversation_history(user_id: str, session_id: str, limit: int = 10) -> List[Dict[str, str]]:
    """
    获取用户的对话历史
    
    参数：
        user_id: 用户ID
        session_id: 会话ID
        limit: 返回的最大对话数
    
    返回：
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
    """
    store = get_memory_store()
    
    # 搜索用户的对话历史
    items = store.search(("users", user_id, "conversations", session_id), limit=limit)
    
    if items:
        history = []
        for item in items:
            if isinstance(item.value, dict):
                history.append(item.value)
        
        logger.info(f"[Memory] 读取用户 {user_id} 会话 {session_id} 的 {len(history)} 条历史")
        return history
    else:
        logger.info(f"[Memory] 用户 {user_id} 会话 {session_id} 无对话历史")
        return []


def save_conversation_turn(user_id: str, session_id: str, role: str, content: str) -> None:
    """
    保存单轮对话
    
    参数：
        user_id: 用户ID
        session_id: 会话ID
        role: "user" 或 "assistant"
        content: 对话内容
    """
    store = get_memory_store()
    
    # 使用时间戳作为 key
    timestamp = str(int(time.time() * 1000))
    
    # 保存到用户的对话历史命名空间
    store.put(
        ("users", user_id, "conversations", session_id),
        timestamp,
        {"role": role, "content": content, "timestamp": timestamp}
    )
    logger.info(f"[Memory] 已记录用户 {user_id} 会话 {session_id} 的对话: {role}={content[:50]}")


def extract_and_update_preferences(
    user_id: str,
    query: str,
    agents_called: List[str],
    final_answer: str,
) -> None:
    """
    从查询、调用的 agents 和答案中提取用户偏好并更新
    
    例如：如果用户频繁问手术室相关问题，记录为关注领域
    """
    # 简单的关键词检测
    focus_keywords = {
        "手术室": "手术室",
        "ICU": "ICU",
        "急诊": "急诊",
        "门诊": "门诊",
        "病房": "病房",
    }
    
    detected_focus = []
    for keyword, focus in focus_keywords.items():
        if keyword in query or keyword in final_answer:
            detected_focus.append(focus)
    
    if detected_focus or agents_called:
        preferences = get_user_preferences(user_id)
        
        # 更新关注领域
        if "focus_areas" not in preferences:
            preferences["focus_areas"] = {}
        
        for focus in detected_focus:
            preferences["focus_areas"][focus] = preferences["focus_areas"].get(focus, 0) + 1
        
        # 更新常用数据源
        if "preferred_agents" not in preferences:
            preferences["preferred_agents"] = {}
        
        for agent in agents_called:
            preferences["preferred_agents"][agent] = preferences["preferred_agents"].get(agent, 0) + 1
        
        save_user_preferences(user_id, preferences)


# ============================================================================
# 节点函数
# ============================================================================

def node_init_context(state: MediArchGraphState) -> Dict[str, Any]:
    """初始化上下文（从 Worker 导入后设置）"""
    # 这个函数会在 build_mediarch_graph 中动态注入 available_workers
    return {}


# ============================================================================
# 辅助函数（子主题与检索强度自适应）
# ============================================================================

def _derive_subtopics(query: str, neo4j_expansion: Dict[str, Any]) -> List[str]:
    """
    基于查询 + Neo4j 扩展实体/领域推导子主题，用于驱动多视角检索。
    - 不硬编码护理类型，完全依赖输入关键词与图谱扩展。
    - 去重并保持顺序，限制最多 6 个。
    """
    candidates: List[str] = []

    # 1) 优先使用 Neo4j 扩展实体名称
    for ent in (neo4j_expansion.get("expanded_entities") or [])[:5]:
        name = (ent.get("name") or "").strip()
        if name:
            candidates.append(name)

    # 2) 结合知识覆盖领域
    for cov in (neo4j_expansion.get("knowledge_coverage") or [])[:4]:
        dom = (cov.get("domain") or "").strip()
        if dom:
            candidates.append(dom)

    # 3) 从原始 query 粗抓名词（简单按中文/英文词片段拆分）
    import re

    for token in re.findall(r"[\u4e00-\u9fa5]{2,6}|[A-Za-z]{3,}", query):
        if token:
            candidates.append(token.strip())

    # 去重保持顺序
    seen = set()
    subtopics = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            subtopics.append(c)
        if len(subtopics) >= 6:
            break

    return subtopics


async def node_general_answer(state: MediArchGraphState) -> Dict[str, Any]:
    """通用回答节点（非医院相关问题）"""
    query = state.get("query", "")
    
    logger.info(f"[MediArchGraph→GeneralAnswer] 非医院相关问题: {query}")
    
    answer = (
        f"您好！您的问题「{query}」似乎不在医院建筑设计的范围内。\n\n"
        "我是专门服务于医院建筑设计的智能助手，可以帮您解答以下问题：\n"
        "- 医院各科室的设计规范和标准\n"
        "- 医疗设备的配置要求\n"
        "- 医院建筑的空间布局和流线设计\n"
        "- 医院建筑的环境控制和节能设计\n\n"
        "如果您有医院建筑设计相关的问题，欢迎随时提问！"
    )
    
    return {
        "final_answer": answer,
        "recommended_questions": [
            "手术室的设计规范和标准是什么？",
            "ICU的设备配置有哪些要求？",
            "医院门诊大厅的空间布局应该如何设计？",
        ],
        # ★修改3：推送 AI 回复到 messages
        "messages": [AIMessage(content=answer)]
    }


def node_gather_responses(state: MediArchGraphState) -> Dict[str, Any]:
    """
    收集 Worker 响应（已由 Adapter 自动收集）
    
    注意：
    - items 已经通过 ItemsAnnotated 自动去重合并
    - worker_responses 已经通过 WorkerResponsesAnnotated 自动追加
    - 这个节点只需要做一些统计和日志
    """
    items = state.get("items", [])
    worker_responses = state.get("worker_responses", [])
    
    logger.info(f"[MediArchGraph→Gather] 收集到 {len(items)} 条去重结果")
    logger.info(f"[MediArchGraph→Gather] 来自 {len(worker_responses)} 个 Worker 的完整响应")
    
    # 统计每个 Worker 的贡献
    worker_stats = {}
    for wr in worker_responses:
        agent_name = wr.get("agent_name", "unknown")
        item_count = wr.get("item_count", 0)
        worker_stats[agent_name] = item_count
    
    logger.info(f"[MediArchGraph→Gather] Worker 统计: {worker_stats}")
    
    return {
        "diagnostics": {
            "total_items": len(items),
            "worker_count": len(worker_responses),
            "worker_stats": worker_stats,
        }
    }


def node_save_memory(state: MediArchGraphState) -> Dict[str, Any]:
    """保存长期记忆和对话历史"""
    user_id = state.get("user_id", "default_user")
    session_id = state.get("session_id", "default_session")
    query = state.get("query", "")
    final_answer = state.get("final_answer", "")
    agents_called = state.get("scheduled_workers", [])
    
    # 保存对话历史
    if query:
        save_conversation_turn(user_id, session_id, "user", query)
    if final_answer:
        save_conversation_turn(user_id, session_id, "assistant", final_answer)
    
    # 更新用户偏好
    extract_and_update_preferences(user_id, query, agents_called, final_answer)

    logger.info(f"[MediArchGraph→SaveMemory] 已保存用户 {user_id} 的记忆")

    # ✅ [FIX 2025-01-16] 返回final_answer供LangGraph Studio Output面板显示
    return {"final_answer": final_answer}


# ============================================================================
# 路由函数
# ============================================================================

def route_after_mark(state: MediArchGraphState) -> str:
    """屏障检查：所有 Worker 完成后进入 gather"""
    active_workers = state.get("active_workers") or []
    expected = len(active_workers)

    completed_names = set(state.get("completed_workers", []) or [])
    completed = len([worker for worker in active_workers if worker in completed_names])
    
    logger.info(f"[MediArchGraph→Barrier] 完成 {completed}/{expected}")
    
    if expected > 0 and completed >= expected:
        return "gather"
    return "noop"


# ============================================================================
# 构建图
# ============================================================================

def _get_worker_workflows() -> Dict[str, Any]:
    """获取所有可用的 Worker workflows"""
    workflows = {}
    
    # 直接从各个 Agent 模块导入已编译的 graph
    try:
        from backend.app.agents.neo4j_agent.agent import graph as neo4j_graph
        workflows["neo4j_agent"] = neo4j_graph
        logger.info("[MediArchGraph] 导入 neo4j_agent.graph")
    except Exception as e:
        logger.warning(f"[MediArchGraph] 无法导入 neo4j_agent.graph: {e}")
    
    try:
        from backend.app.agents.milvus_agent.agent import graph as milvus_graph
        workflows["milvus_agent"] = milvus_graph
        logger.info("[MediArchGraph] 导入 milvus_agent.graph")
    except Exception as e:
        logger.warning(f"[MediArchGraph] 无法导入 milvus_agent.graph: {e}")
    
    try:
        from backend.app.agents.mongodb_agent.agent import graph as mongodb_graph
        workflows["mongodb_agent"] = mongodb_graph
        logger.info("[MediArchGraph] 导入 mongodb_agent.graph")
    except Exception as e:
        logger.warning(f"[MediArchGraph] 无法导入 mongodb_agent.graph: {e}")
    
    try:
        from backend.app.agents.online_search_agent.agent import graph as online_search_graph
        workflows["online_search_agent"] = online_search_graph
        logger.info("[MediArchGraph] 导入 online_search_agent.graph")
    except Exception as e:
        logger.warning(f"[MediArchGraph] 无法导入 online_search_agent.graph: {e}")
    
    return workflows


# ============================================================================
# [NEW] 2025-01-16: 异步Checkpointer创建函数（彻底解决阻塞调用问题）
# ============================================================================

def create_async_checkpointer():
    """
    创建checkpointer用于持久化。
    """
    if CHECKPOINTER_RUNTIME_STATUS["effective_backend"] == "platform":
        logger.info("[MediArchGraph->Checkpointer] LangGraph API环境，使用平台内置持久化")
        return None  # LangGraph API会自动处理

    checkpointer = create_checkpointer_from_runtime(
        CHECKPOINTER_RUNTIME_STATUS,
        sqlite_path=SQLITE_CHECKPOINT_PATH,
        postgres_uri=POSTGRES_CHECKPOINT_URI,
    )
    logger.info(
        "[MediArchGraph->Checkpointer] 使用 %s（configured=%s, effective=%s, fallback_reason=%s）",
        type(checkpointer).__name__ if checkpointer is not None else "None",
        CHECKPOINTER_RUNTIME_STATUS["configured_backend"],
        CHECKPOINTER_RUNTIME_STATUS["effective_backend"],
        CHECKPOINTER_RUNTIME_STATUS["fallback_reason"],
    )
    return checkpointer


# ============================================================================
# MediArch Graph 构建
# ============================================================================

def build_mediarch_graph():
    """
    构建 MediArch Graph 图 - 完整版本
    
    核心功能：
    - ✅ 使用 base_agent 的标准 Reducer（无重复代码）
    - ✅ 使用 create_worker_adapter 包装 Worker
    - ✅ worker_responses 字段（Synthesizer 可获取完整信息）
    - ✅ Human-in-the-Loop（interrupt 机制）
    - ✅ 内部循环（Synthesizer 重试）
    - ✅ 外部循环（重新调用 Workers）
    - ✅ 对话历史管理
    - ✅ LLM 自动分类用户反馈
    """
    builder = StateGraph(MediArchGraphState)

    # ★修改2：新增聊天入口节点
    def node_chat_entry(state: MediArchGraphState) -> Dict[str, Any]:
        # 优先从 request 中获取 query（API 调用）
        request = state.get("request")
        query = ""

        if request and hasattr(request, 'query') and request.query:
            query = request.query
            logger.info(f"[MediArchGraph->ChatEntry] 从 request 获取 query: {query[:50]}...")
        else:
            # 降级：从 messages 里的最后一句 HumanMessage 提取
            msgs = state.get("messages", [])
            for m in reversed(msgs):
                if isinstance(m, HumanMessage):
                    query = m.content
                    logger.info(f"[MediArchGraph->ChatEntry] 从 messages 获取 query: {query[:50]}...")
                    break

        # 若没有 request，根据 query 创建一个
        if request is None and query:
            request = AgentRequest(
                query=query,
                filters={},
                top_k=DEFAULT_TOP_K,
                lang="zh",
                timeout_ms=DEFAULT_TIMEOUT_MS,
                trace_id=None,
                metadata={},
                context=[],
                attachments=[],
            )

        return {
            "query": query,
            "original_query": query,  # 在 Orchestrator 改写前保存原始查询
            "request": request,
            "user_id": state.get("user_id", "studio_user"),
            "session_id": state.get("session_id", "studio_session"),
            "feedback_round": state.get("feedback_round", 0),
        }

    builder.add_node("chat_entry", node_chat_entry)
    
    # ========== 获取 Worker 并创建 Adapter ==========
    worker_graphs = _get_worker_workflows()
    workers_added: List[str] = []
    
    for worker_name, worker_graph in worker_graphs.items():
        try:
            # ✅ 使用 base_agent 的标准 Adapter
            adapter_node = create_worker_adapter(worker_name, worker_graph)
            builder.add_node(worker_name, adapter_node)
            workers_added.append(worker_name)
            logger.info(f"[MediArchGraph] 使用标准 Adapter 添加 Worker: {worker_name}")
        except Exception as e:
            logger.error(f"[MediArchGraph] 添加 {worker_name} 失败: {e}")
    
    logger.info(f"[MediArchGraph] 共添加 {len(workers_added)} 个 Worker: {workers_added}")
    
    # ========== 添加核心节点 ==========
    
    # 1. 上下文初始化
    def node_init_context_impl(_: MediArchGraphState) -> Dict[str, Any]:
        diagnostics = {}
        diagnostics.update(
            build_phase1_runtime_diagnostics(PHASE1_RUNTIME_MODE["configured_mode"])
        )
        diagnostics.update(
            build_checkpointer_runtime_diagnostics(CHECKPOINTER_RUNTIME_STATUS)
        )
        diagnostics.update(
            build_store_runtime_diagnostics(STORE_RUNTIME_STATUS)
        )
        try:
            from backend.app.services.query_expansion import get_query_expansion_runtime_status

            diagnostics["query_expansion_runtime"] = get_query_expansion_runtime_status()
        except Exception:
            pass

        return {
            "available_workers": workers_added,
            "diagnostics": diagnostics,
        }
    
    builder.add_node("init_context", node_init_context_impl)
    
    # 2. Orchestrator Logic 子图
    builder.add_node("orchestrator_agent", orchestrator_logic_graph)
    
    # 3. 通用回答节点
    builder.add_node("general_answer", node_general_answer)
    
    # 4. 并行 Worker 调度节点
    def node_prepare_parallel_workers(state: MediArchGraphState) -> Dict[str, Any]:
        """根据可用 Worker 并行调度，并预先生成启发式扩展供各检索器使用。"""
        query = state.get("query", "")
        available = state.get("available_workers") or workers_added
        requested_workers = state.get("agents_to_call")

        if not state.get("is_hospital_related", True):
            logger.info("[MediArchGraph→PrepareParallel] 问题不属于医院建筑领域，跳过 Worker 调用")
            return {
                "scheduled_workers": [],
                "active_workers": [],
                "neo4j_expansion": {},
                "subtopics": [],
            }

        # [STRICT DOC SCOPE] 若用户显式要求“仅基于指定资料/不要引用其它资料”，则禁用可能跨资料扩展的 Worker（如 Neo4j/OnlineSearch）
        strict_cross_doc_request = False
        strict_doc_ids: List[str] = []
        strict_source_documents: List[str] = []
        strict_original_query = state.get("original_query") or query or ""
        try:
            request = state.get("request")
            filters = request.filters if request else {}
            doc_ids = filters.get("doc_ids") or filters.get("doc_id") or []
            source_docs = filters.get("source_documents") or filters.get("source_document") or []
            strict_doc_ids = [str(d).strip() for d in (doc_ids if isinstance(doc_ids, list) else [doc_ids]) if str(d).strip()]
            strict_source_documents = [str(s).strip() for s in (source_docs if isinstance(source_docs, list) else [source_docs]) if str(s).strip()]
            has_scope = bool(strict_doc_ids or strict_source_documents)
            raw_query = strict_original_query
            if request and getattr(request, "metadata", None):
                raw_query = (request.metadata or {}).get("original_query") or raw_query
            strict_original_query = raw_query or strict_original_query
            wants_strict = any(k in raw_query for k in ("仅基于", "只基于", "不要引用", "交叉验证", "每条都必须带引用"))
            strict_cross_doc_request = bool(has_scope and wants_strict)
        except Exception as exc:
            logger.warning("[MediArchGraph→PrepareParallel] strict_doc_scope 检测失败: %s", exc)

        workers = select_workers_for_execution(
            available_workers=available,
            agents_to_call=requested_workers,
            priority=DEFAULT_WORKER_PRIORITY,
            strict_cross_doc_request=strict_cross_doc_request,
        )

        # [Benchmark] retrieval_mode 过滤: R0=Milvus-only, R1=Neo4j+Milvus, R2=全部
        _rm_request = state.get("request")
        _retrieval_mode = ((_rm_request.metadata or {}).get("retrieval_mode", "R2") if _rm_request and getattr(_rm_request, "metadata", None) else "R2").upper()
        if _retrieval_mode == "R0":
            workers = [w for w in workers if w == "milvus_agent"]
        elif _retrieval_mode == "R1":
            workers = [w for w in workers if w in ("neo4j_agent", "milvus_agent")]

        logger.info(
            "[MediArchGraph→PrepareParallel] requested=%s, available=%s, scheduled=%s, retrieval_mode=%s",
            requested_workers,
            available,
            workers,
            _retrieval_mode,
        )
        if strict_cross_doc_request:
            logger.info("[MediArchGraph→PrepareParallel] strict_doc_scope=on, scheduled_workers=%s", workers)

        if not workers:
            logger.warning("[MediArchGraph→PrepareParallel] 没有可用 Worker")
            return {
                "scheduled_workers": [],
                "active_workers": [],
                "neo4j_expansion": {},
                "subtopics": [],
            }

        # 启发式扩展（为 Milvus / MongoDB 提供初始搜索词）
        try:
            from backend.app.services.query_expansion import expand_query

            expansion_result = expand_query(
                query,
                include_synonyms=True,
                include_ngrams=True,
                max_search_terms=25,
            )

            neo4j_expansion: Dict[str, Any] = {
                "expanded_entities": [
                    {"name": kw, "type": "QueryExpansion", "score": 0.7}
                    for kw in expansion_result.keywords[:8]
                ],
                "expanded_relations": [],
                "knowledge_coverage": [{"domain": "医院建筑", "count": len(expansion_result.keywords)}],
                "search_terms": expansion_result.search_terms[:20],
                "original_query": query,
                "query_type": "entity",
                "fallback_mode": True,
            }
            logger.info("[MediArchGraph→PrepareParallel] QueryExpansion 生成 %s 个搜索词", len(neo4j_expansion["search_terms"]))
        except Exception as exc:
            logger.warning(f"[MediArchGraph→PrepareParallel] QueryExpansion 失败: {exc}，使用基础扩展")
            neo4j_expansion = {
                "expanded_entities": [{"name": query, "type": "QueryExpansion", "score": 0.7}],
                "expanded_relations": [],
                "knowledge_coverage": [],
                "search_terms": [query],
                "original_query": query,
                "query_type": "entity",
                "fallback_mode": True,
            }

        subtopics = _derive_subtopics(query, neo4j_expansion)

        request = state.get("request")
        updated_request = request
        if request:
            new_context = list(request.context or [])
            if neo4j_expansion.get("search_terms"):
                new_context.append(f"扩展搜索词: {', '.join(neo4j_expansion['search_terms'][:5])}")
            if subtopics:
                new_context.append(f"子主题: {', '.join(subtopics)}")

            new_metadata = dict(request.metadata or {})
            new_metadata["neo4j_expansion"] = neo4j_expansion
            if subtopics:
                new_metadata["subtopics"] = subtopics

            updated_request = AgentRequest(
                query=request.query,
                filters=request.filters,
                top_k=request.top_k,
                lang=request.lang,
                timeout_ms=request.timeout_ms,
                trace_id=request.trace_id,
                metadata=new_metadata,
                context=new_context,
                attachments=request.attachments,
            )

        return {
            "scheduled_workers": workers,
            "active_workers": workers,
            "neo4j_expansion": neo4j_expansion,
            "subtopics": subtopics,
            "request": updated_request,
            "strict_cross_doc_request": strict_cross_doc_request,
            "strict_doc_ids": strict_doc_ids,
            "strict_source_documents": strict_source_documents,
            "original_query": strict_original_query,
        }

    def node_fan_out_workers(_: MediArchGraphState) -> Dict[str, Any]:
        """占位节点，用于触发并行 Worker。"""
        return {}

    def node_extract_neo4j_expansion(state: MediArchGraphState) -> Dict[str, Any]:
        """
        Worker 收敛后，从 Neo4j diagnostics 提取真实扩展结果，供后续循环或 Synthesizer 使用。
        """
        worker_responses = state.get("worker_responses", [])
        query = state.get("query", "")
        current_expansion = state.get("neo4j_expansion", {}) or {}

        for resp in worker_responses:
            if resp.get("agent_name") != "neo4j_agent":
                continue

            diagnostics = resp.get("diagnostics", {})
            query_path = diagnostics.get("query_path")
            if not query_path:
                continue

            current_expansion = {
                "expanded_entities": query_path.get("expanded_entities", []),
                "expanded_relations": query_path.get("expanded_relations", []),
                "knowledge_coverage": query_path.get("knowledge_coverage", []),
                "search_terms": query_path.get("search_terms", []),
                "original_query": query_path.get("original_query", query),
                "query_type": query_path.get("query_type", "entity"),
                "fallback_mode": False,
            }
            logger.info(
                "[MediArchGraph→ExtractExpansion] 采纳 Neo4j 扩展: %s 个实体 / %s 条关系",
                len(current_expansion.get("expanded_entities", [])),
                len(current_expansion.get("expanded_relations", [])),
            )
            break

        subtopics = _derive_subtopics(query, current_expansion)

        request = state.get("request")
        updated_request = request
        if request and current_expansion:
            new_metadata = dict(request.metadata or {})
            new_metadata["neo4j_expansion"] = current_expansion
            if subtopics:
                new_metadata["subtopics"] = subtopics

            updated_request = AgentRequest(
                query=request.query,
                filters=request.filters,
                top_k=request.top_k,
                lang=request.lang,
                timeout_ms=request.timeout_ms,
                trace_id=request.trace_id,
                metadata=new_metadata,
                context=request.context,
                attachments=request.attachments,
            )

        return {
            "neo4j_expansion": current_expansion,
            "subtopics": subtopics,
            "request": updated_request,
        }

# ========== 关键节点定义（移到条件外，确保始终可用）==========
# 这些节点必须在边定义之前添加

    # 4. 并行调度节点
    builder.add_node("prepare_parallel_workers", node_prepare_parallel_workers)
    builder.add_node("fan_out_workers", node_fan_out_workers)
    builder.add_node("extract_neo4j_expansion", node_extract_neo4j_expansion)

    # ========== 2025-11-25 新增：Knowledge Fusion 节点 ==========
    def node_knowledge_fusion(state: MediArchGraphState) -> Dict[str, Any]:
        """
        Knowledge Fusion 节点：合并 Neo4j 和 Milvus 的并行检索结果

        核心功能:
        1. 从 worker_responses 中分离 Neo4j 和 Milvus 的结果
        2. 调用 fuse_retrieval_results 进行融合
        3. 生成 unified_hints 供 MongoDB 精确定位
        4. 生成 answer_graph_data 供前端可视化

        输出:
        - unified_hints: 统一检索线索
        - answer_graph_data: 答案图谱数据
        - items: 合并后的 items（用于后续处理）
        """
        query = state.get("query", "")
        worker_responses = state.get("worker_responses", [])

        logger.info(f"[MediArchGraph→KnowledgeFusion] 开始融合，共 {len(worker_responses)} 个 Worker 响应")

        # 1. 检查缓存（加入 session_id 避免不同会话/上下文误命中）
        cache = get_retrieval_cache()
        request = state.get("request")
        filters = request.filters if request else None
        session_id = state.get("session_id", "")
        cache_filters = {**(filters or {}), "_sid": session_id} if session_id else filters

        cached_fusion = cache.get(query, cache_filters, cache_type="fusion")
        if cached_fusion is not None:
            logger.info("[MediArchGraph→KnowledgeFusion] 命中缓存，直接使用融合结果")
            return {
                "unified_hints": cached_fusion.get("unified_hints", {}),
                "answer_graph_data": cached_fusion.get("answer_graph_data", {}),
                "items": cached_fusion.get("merged_items", []),
                "neo4j_items": cached_fusion.get("neo4j_items", []),
                "milvus_items": cached_fusion.get("milvus_items", []),
                "cache_hit": True,
                "diagnostics": {"fusion_cache_hit": True},
            }

        # 2. 分离 Neo4j 和 Milvus 的结果
        neo4j_items: List[AgentItem] = []
        milvus_items: List[AgentItem] = []

        for resp in worker_responses:
            agent_name = resp.get("agent_name", "")
            items = resp.get("items", [])

            if agent_name == "neo4j_agent":
                neo4j_items.extend(items)
                logger.info(f"[MediArchGraph→KnowledgeFusion] Neo4j 贡献: {len(items)} items")
            elif agent_name == "milvus_agent":
                milvus_items.extend(items)
                logger.info(f"[MediArchGraph→KnowledgeFusion] Milvus 贡献: {len(items)} items")

        # 3. 调用 Knowledge Fusion
        try:
            fusion_result: KnowledgeFusionResult = fuse_retrieval_results(
                neo4j_items=neo4j_items,
                milvus_items=milvus_items,
                query=query,
                max_entities=20,
                max_chunks=30,
            )

            # 提取结果
            unified_hints_dict = {
                "entity_names": fusion_result.unified_hints.entity_names,
                "entity_types": fusion_result.unified_hints.entity_types,
                "chunk_ids": fusion_result.unified_hints.chunk_ids,
                "sections": fusion_result.unified_hints.sections,
                "page_ranges": fusion_result.unified_hints.page_ranges,
                "relations": fusion_result.unified_hints.relations,
                "search_terms": fusion_result.unified_hints.search_terms,
                "neo4j_entity_count": fusion_result.unified_hints.neo4j_entity_count,
                "milvus_chunk_count": fusion_result.unified_hints.milvus_chunk_count,
                "fusion_score": fusion_result.unified_hints.fusion_score,
            }

            graph_data_dict = fusion_result.graph_data.to_dict()
            merged_items = fusion_result.merged_items

            logger.info(
                f"[MediArchGraph→KnowledgeFusion] 融合完成: "
                f"entities={len(unified_hints_dict['entity_names'])}, "
                f"chunks={len(unified_hints_dict['chunk_ids'])}, "
                f"graph_nodes={len(graph_data_dict['nodes'])}, "
                f"score={unified_hints_dict['fusion_score']:.2f}"
            )

            # 4. 保存缓存
            cache_data = {
                "unified_hints": unified_hints_dict,
                "answer_graph_data": graph_data_dict,
                "merged_items": merged_items,
                "neo4j_items": neo4j_items,
                "milvus_items": milvus_items,
            }
            cache.set(query, cache_filters, cache_data, cache_type="fusion", ttl=300)

            # 5. 更新 request.metadata，注入 unified_hints 供 MongoDB 使用
            updated_request = request
            if request:
                new_metadata = dict(request.metadata or {})
                new_metadata["unified_hints"] = unified_hints_dict
                new_metadata["answer_graph_data"] = graph_data_dict

                updated_request = AgentRequest(
                    query=request.query,
                    filters=request.filters,
                    top_k=request.top_k,
                    lang=request.lang,
                    timeout_ms=request.timeout_ms,
                    trace_id=request.trace_id,
                    metadata=new_metadata,
                    context=request.context,
                    attachments=request.attachments,
                )

            return {
                "unified_hints": unified_hints_dict,
                "answer_graph_data": graph_data_dict,
                "items": merged_items,
                "neo4j_items": neo4j_items,
                "milvus_items": milvus_items,
                "request": updated_request,
                "cache_hit": False,
                "diagnostics": fusion_result.diagnostics,
            }

        except Exception as e:
            logger.error(f"[MediArchGraph→KnowledgeFusion] 融合失败: {e}")
            # 降级：直接合并 items
            all_items = neo4j_items + milvus_items
            return {
                "unified_hints": {},
                "answer_graph_data": {},
                "items": all_items,
                "neo4j_items": neo4j_items,
                "milvus_items": milvus_items,
                "cache_hit": False,
                "diagnostics": {"fusion_error": str(e)},
            }

    builder.add_node("knowledge_fusion", node_knowledge_fusion)

    # ========== 2025-11-25 新增：并行检索阶段1的屏障节点 ==========
    def node_phase1_barrier(state: MediArchGraphState) -> Dict[str, Any]:
        """
        阶段1屏障：等待 Neo4j 和 Milvus 都完成

        LangGraph 并行执行模型：
        - add_conditional_edges 返回 List[str] 时使用 fan-out/fan-in 语义
        - 所有并行分支完成并合并状态后，才触发此节点（只调用一次）
        - 因此 completed_workers 在此处已包含所有 phase1 workers
        - 下方 "还有 worker 没完成" 分支在当前架构下不会执行（保留作为防御性代码）
        """
        completed = set(state.get("completed_workers", []) or [])
        scheduled = state.get("scheduled_workers", []) or []

        # 计算阶段1需要完成的 workers
        phase1_expected = {"neo4j_agent", "milvus_agent"}
        phase1_scheduled = phase1_expected.intersection(set(scheduled))

        # 检查阶段1调度的 workers 是否都完成
        phase1_completed = phase1_scheduled.intersection(completed)
        phase1_done = len(phase1_completed) >= len(phase1_scheduled) and len(phase1_scheduled) > 0

        logger.info(
            f"[MediArchGraph->Phase1Barrier] "
            f"scheduled={phase1_scheduled}, completed={phase1_completed}, "
            f"phase1_done={phase1_done}"
        )

        if phase1_done:
            # 所有阶段1 workers 都完成，进入融合阶段
            return {"parallel_retrieval_phase": "phase2_fusion"}
        else:
            # 还有 worker 没完成，保持等待状态
            return {"parallel_retrieval_phase": "phase1_parallel"}

    builder.add_node("phase1_barrier", node_phase1_barrier)

    # ========== 2025-11-25 新增：阶段2 MongoDB 调度节点 ==========
    def node_schedule_mongodb(state: MediArchGraphState) -> Dict[str, Any]:
        """
        调度 MongoDB Agent 进行精确定位

        使用 Knowledge Fusion 生成的 unified_hints
        """
        # [Benchmark] R0/R1 模式跳过 MongoDB
        _rm_req = state.get("request")
        _rm_mode = ((_rm_req.metadata or {}).get("retrieval_mode", "R2") if _rm_req and getattr(_rm_req, "metadata", None) else "R2").upper()
        if _rm_mode in ("R0", "R1"):
            logger.info("[MediArchGraph→ScheduleMongoDB] retrieval_mode=%s, skip MongoDB", _rm_mode)
            return {"active_workers": []}

        unified_hints = state.get("unified_hints", {})
        available = state.get("available_workers", [])
        strict_cross_doc_request = bool(state.get("strict_cross_doc_request"))
        strict_doc_ids = state.get("strict_doc_ids") or []
        strict_source_documents = state.get("strict_source_documents") or []
        strict_has_scope = bool(strict_doc_ids or strict_source_documents)

        if "mongodb_agent" not in available:
            logger.warning("[MediArchGraph→ScheduleMongoDB] mongodb_agent 不可用")
            return {
                "active_workers": [],
            }

        # 检查是否有 chunk_ids 需要定位
        chunk_ids = unified_hints.get("chunk_ids", [])
        entity_names = unified_hints.get("entity_names", [])

        if strict_cross_doc_request and strict_has_scope:
            # 严格资料范围回归：即使 Knowledge Fusion 未给出 chunk_ids/entity_names，
            # 也要在指定 doc scope 内执行 MongoDB 检索，确保 citations 可对齐且不泄漏。
            logger.info(
                "[MediArchGraph→ScheduleMongoDB] strict_cross_doc_request=on，强制调用 MongoDB（doc_scope=%s）",
                len(strict_doc_ids) if strict_doc_ids else len(strict_source_documents),
            )
        elif not chunk_ids and not entity_names:
            logger.info("[MediArchGraph→ScheduleMongoDB] 无需调用 MongoDB（无 chunk_ids 或 entity_names）")
            return {
                "active_workers": [],
            }

        logger.info(
            f"[MediArchGraph→ScheduleMongoDB] 调度 MongoDB Agent: "
            f"chunk_ids={len(chunk_ids)}, entity_names={len(entity_names)}"
        )

        # 重置 completed_workers，避免 phase1 的累积值干扰 phase2 的 barrier 检查
        # 注意：completed_workers 使用 add reducer，这里用负值列表抵消
        # 但更安全的做法是在 route_after_mark 中只检查 active_workers
        return {
            "active_workers": ["mongodb_agent"],
            "parallel_retrieval_phase": "phase3_mongodb",
        }

    builder.add_node("schedule_mongodb", node_schedule_mongodb)

    def node_schedule_online_search(state: MediArchGraphState) -> Dict[str, Any]:
        """在本地检索阶段后按需调度 Online Search。"""
        scheduled = state.get("scheduled_workers", []) or []
        available = state.get("available_workers", []) or []

        if "online_search_agent" not in scheduled or "online_search_agent" not in available:
            logger.info("[MediArchGraph→ScheduleOnlineSearch] 无需调用 Online Search")
            return {
                "active_workers": [],
            }

        logger.info("[MediArchGraph→ScheduleOnlineSearch] 调度 Online Search Agent")
        return {
            "active_workers": ["online_search_agent"],
        }

    builder.add_node("schedule_online_search", node_schedule_online_search)

    # 5. Gather Responses 节点
    builder.add_node("gather_responses", node_gather_responses)

    # 6. Result Synthesizer 子图
    try:
        from backend.app.agents.result_synthesizer_agent.agent import graph as synth_graph
        builder.add_node("result_synthesizer_agent", synth_graph)
        logger.info("[MediArchGraph] 添加了 result_synthesizer_agent 子图节点")
    except Exception as e:
        logger.warning(f"[MediArchGraph] 无法导入 result_synthesizer_agent: {e}")

    # 7. 记忆保存节点
    builder.add_node("save_memory", node_save_memory)

    # 8. 空节点（用于并行 fan-out 的 fallback）
    def node_noop(_: MediArchGraphState) -> Dict[str, Any]:
        return {}

    builder.add_node("noop", node_noop)
    
    # ========== 设置边（2025-11-25 真正并行检索架构）==========
    #
    # 新架构流程：
    # 1. chat_entry → init_context → orchestrator_agent → prepare_parallel_workers
    # 2. [阶段1: 真正并行] fan_out → [neo4j_agent, milvus_agent] 并行执行
    # 3. phase1_barrier (等待 Neo4j 和 Milvus 都完成)
    # 4. knowledge_fusion (融合两边结果，生成 unified_hints)
    # 5. [阶段2: 精确定位] schedule_mongodb → mongodb_agent
    # 6. gather_responses → result_synthesizer_agent → END
    #

    # 主流程：统一入口为 chat_entry
    builder.set_entry_point("chat_entry")
    builder.add_edge("chat_entry", "init_context")
    builder.add_edge("init_context", "orchestrator_agent")
    builder.add_edge("orchestrator_agent", "prepare_parallel_workers")

    # 定义阶段1的 Workers（Neo4j + Milvus 真正并行）
    PHASE1_WORKERS = ["neo4j_agent", "milvus_agent"]

    # 定义阶段2的 Workers（使用 unified_hints 进行精确定位）
    PHASE2_WORKERS = ["mongodb_agent"]

    # 定义可选 Workers（online_search 等）
    OPTIONAL_WORKERS = ["online_search_agent"]

    # 依据是否有可用 worker，决定走通用回答还是并行 fan-out
    def route_after_prepare(state: MediArchGraphState) -> str:
        """并行模式：Neo4j + Milvus 同时执行"""
        is_hospital_related = state.get("is_hospital_related", True)
        if not is_hospital_related:
            return "general_answer"

        scheduled = state.get("scheduled_workers", []) or []
        phase1_available = [w for w in scheduled if w in PHASE1_WORKERS]
        if not phase1_available:
            return "general_answer"

        logger.info("[MediArchGraph->Route] 并行模式: Neo4j + Milvus 同时启动")
        return "fan_out_phase1"

    prepare_route_mapping: Dict[str, str] = {
        "general_answer": "general_answer",
        "fan_out_phase1": "fan_out_workers",
    }

    builder.add_conditional_edges("prepare_parallel_workers", route_after_prepare, prepare_route_mapping)

    # GeneralAnswer → save_memory → END
    builder.add_edge("general_answer", "save_memory")

    # ========== 阶段1：Neo4j + Milvus 并行 ==========
    if workers_added:
        # Parallel mode (default): Neo4j + Milvus run concurrently → phase1_barrier → Knowledge Fusion
        phase1_workers_available = [w for w in PHASE1_WORKERS if w in workers_added]

        def route_phase1_workers(state: MediArchGraphState) -> List[str]:
            """只路由 Neo4j 和 Milvus 进行并行检索"""
            scheduled = state.get("scheduled_workers", []) or []
            phase1_to_run = [w for w in scheduled if w in PHASE1_WORKERS]
            logger.info(f"[MediArchGraph->Phase1] 并行启动: {phase1_to_run}")
            return phase1_to_run if phase1_to_run else ["noop"]

        phase1_route_mapping: Dict[str, str] = {worker: worker for worker in phase1_workers_available}
        phase1_route_mapping["noop"] = "noop"

        builder.add_conditional_edges("fan_out_workers", route_phase1_workers, phase1_route_mapping)

        for worker in phase1_workers_available:
            builder.add_edge(worker, "phase1_barrier")

    # ========== 阶段1屏障 → Knowledge Fusion ==========
    def route_after_phase1_barrier(state: MediArchGraphState) -> str:
        """检查阶段1是否完成，决定是否进入融合"""
        phase = state.get("parallel_retrieval_phase", "")
        if phase == "phase2_fusion":
            return "knowledge_fusion"
        # 还有 worker 没完成，继续等待
        return "noop"

    builder.add_conditional_edges(
        "phase1_barrier",
        route_after_phase1_barrier,
        {
            "knowledge_fusion": "knowledge_fusion",
            "noop": "noop",
        }
    )

    # ========== Knowledge Fusion → 阶段2调度 ==========
    builder.add_edge("knowledge_fusion", "schedule_mongodb")

    # ========== 阶段2：MongoDB 精确定位 ==========
    def route_after_schedule_mongodb(state: MediArchGraphState) -> str:
        """根据 schedule_mongodb 的结果决定是否调用 MongoDB"""
        active_workers = state.get("active_workers", []) or []
        if "mongodb_agent" in active_workers:
            return "mongodb_agent"
        # 无需调用 MongoDB，转入可选在线搜索阶段
        return "schedule_online_search"

    def route_after_schedule_online_search(state: MediArchGraphState) -> str:
        """根据调度结果决定是否调用 Online Search。"""
        active_workers = state.get("active_workers", []) or []
        if "online_search_agent" in active_workers:
            return "online_search_agent"
        return "gather_responses"

    # 只有当 mongodb_agent 可用时才添加路由
    if "mongodb_agent" in workers_added:
        builder.add_conditional_edges(
            "schedule_mongodb",
            route_after_schedule_mongodb,
            {
                "mongodb_agent": "mongodb_agent",
                "schedule_online_search": "schedule_online_search",
            }
        )
        builder.add_edge("mongodb_agent", "schedule_online_search")
    else:
        # MongoDB 不可用，直接进入可选在线搜索阶段
        builder.add_edge("schedule_mongodb", "schedule_online_search")

    if "online_search_agent" in workers_added:
        builder.add_conditional_edges(
            "schedule_online_search",
            route_after_schedule_online_search,
            {
                "online_search_agent": "online_search_agent",
                "gather_responses": "gather_responses",
            }
        )
        builder.add_edge("online_search_agent", "gather_responses")
    else:
        builder.add_edge("schedule_online_search", "gather_responses")

    # ========== Gather → Synthesizer ==========
    def node_push_answer_message(state: MediArchGraphState) -> Dict[str, Any]:
        ans = state.get("final_answer", "")
        if not ans:
            return {}
        return {"messages": [AIMessage(content=ans)]}

    builder.add_node("push_answer_message", node_push_answer_message)

    # Gather → 提取 Neo4j 扩展 → Synthesizer → Push → Save Memory → END
    builder.add_edge("gather_responses", "extract_neo4j_expansion")
    builder.add_edge("extract_neo4j_expansion", "result_synthesizer_agent")
    builder.add_edge("result_synthesizer_agent", "push_answer_message")
    builder.add_edge("push_answer_message", "save_memory")

    # Noop → END（用于等待或跳过）
    builder.add_edge("noop", END)

    if not workers_added:
        logger.warning("[MediArchGraph] 未找到可用 Worker")

    # save_memory → END
    builder.add_edge("save_memory", END)

    # ========== 编译图 ==========

    # 创建完全异步的checkpointer
    checkpointer = create_async_checkpointer()
    store = get_memory_store()

    # 根据checkpointer是否可用决定编译方式
    if checkpointer is None:
        # LangGraph API环境，平台自动处理持久化
        compiled_graph = builder.compile(store=store)
        logger.info("[MediArchGraph] 图编译完成（LangGraph API环境，使用平台内置checkpointer + 自定义store）")
    else:
        # 本地环境，使用自定义checkpointer
        compiled_graph = builder.compile(checkpointer=checkpointer, store=store)
        logger.info(
            f"[MediArchGraph] 图编译完成（使用 {type(checkpointer).__name__} + {type(store).__name__}）"
        )

    return compiled_graph


# ============================================================================
# 导出图（供 LangGraph Studio 使用）
# ============================================================================

graph = build_mediarch_graph()

logger.info("[MediArchGraph] 图构建完成")
