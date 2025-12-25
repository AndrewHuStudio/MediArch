"""Orchestrator Agent - 优化版本

核心改进：
- ✅ 使用 LLMManager（线程安全）
- ✅ 精简代码结构
- ✅ 规范接口（与 supervisor 对接）
- ✅ 删除冗余功能
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from backend.app.agents.base_agent import AgentRequest, get_llm_manager

logger = logging.getLogger("orchestrator_agent")

# 默认配置
DEFAULT_WORKERS = ["neo4j_agent", "milvus_agent", "mongodb_agent", "online_search_agent"]
DEFAULT_TOP_K = 12
DEFAULT_TIMEOUT_MS = 3000


# ============================================================================
# 状态定义
# ============================================================================

class OrchestratorState(TypedDict, total=False):
    """Orchestrator 状态"""
    # 输入
    messages: List[BaseMessage]
    query: str
    available_workers: List[str]
    
    # 内部处理
    extracted_query: str
    
    # 输出
    is_hospital_related: bool
    general_answer: str
    agents_to_call: List[str]
    rewritten_query: str  # 查询改写
    request: AgentRequest
    diagnostics: Dict[str, Any]


# ============================================================================
# LLM 管理
# ============================================================================

def _init_orchestrator_llm():
    """初始化 Orchestrator LLM"""
    api_key = os.getenv("ORCHESTRATOR_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("缺少 ORCHESTRATOR_API_KEY 或 OPENAI_API_KEY")
    
    base_url = os.getenv("ORCHESTRATOR_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    base_url = base_url.rstrip("/") if base_url else None
    model = os.getenv("ORCHESTRATOR_MODEL", "gpt-4o-mini")
    model_provider = os.getenv("ORCHESTRATOR_MODEL_PROVIDER") or os.getenv("OPENAI_MODEL_PROVIDER") or "openai"
    
    return init_chat_model(
        model=model,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=12000,
        timeout=30,
    )


async def get_orchestrator_llm():
    """获取 Orchestrator LLM（异步版本，修复阻塞调用问题）

    2025-11-18: 使用asyncio.to_thread()包装同步LLM初始化，
    避免LangGraph dev的阻塞调用检测。
    """
    import asyncio

    manager = get_llm_manager()

    # 检查是否已缓存
    if "orchestrator" in manager._instances:
        return manager._instances["orchestrator"]

    # 使用asyncio.to_thread()在独立线程中初始化LLM
    try:
        llm = await asyncio.to_thread(_init_orchestrator_llm)
        manager._instances["orchestrator"] = llm
        return llm
    except Exception as e:
        logger.warning(f"[OrchestratorAgent] LLM初始化失败: {e}")
        raise


# ============================================================================
# 辅助函数
# ============================================================================

def extract_query_from_messages(messages: List[BaseMessage]) -> str:
    """提取最后一条用户消息"""
    if not messages:
        return ""
    
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, str):
                return content.strip()
            elif isinstance(content, list):
                # 提取文本块
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") in {"text", "input_text"}:
                            text = block.get("text") or block.get("value") or ""
                            if text:
                                text_parts.append(text)
                return "\n".join(text_parts).strip()
        elif isinstance(msg, dict) and msg.get("type") == "human":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content.strip()
    
    return ""


# ============================================================================
# 节点函数
# ============================================================================

async def node_extract_query(state: OrchestratorState) -> Dict[str, Any]:
    """提取用户查询"""
    messages = state.get("messages", [])
    query = state.get("query", "")
    
    # 如果 state 中已有 query，直接使用
    if query:
        extracted_query = query
    else:
        # 从 messages 中提取
        extracted_query = extract_query_from_messages(messages)
    
    logger.info(f"[Orchestrator→ExtractQuery] 提取查询: {extracted_query}")
    
    return {"extracted_query": extracted_query}


async def node_analyze_intent(state: OrchestratorState) -> Dict[str, Any]:
    """分析用户意图"""
    query = state.get("extracted_query", "")
    messages = state.get("messages", [])
    
    logger.info(f"[Orchestrator→AnalyzeIntent] 分析意图: {query}")
    
    # 构建上下文（最近3轮对话）
    recent_context = []
    for msg in messages[-6:]:  # 最近3轮（每轮user+assistant）
        if isinstance(msg, (HumanMessage, SystemMessage)):
            content = msg.content if isinstance(msg.content, str) else ""
            if content:
                recent_context.append(f"{msg.__class__.__name__}: {content[:100]}")
    
    context_str = "\n".join(recent_context[-4:]) if recent_context else "无上下文"
    
    # System prompt
    system_prompt = """你是 MediArch 综合医院设计助手的意图分析专家。

你的任务：
1. 判断问题是否与医院建筑设计相关
2. 如果问题有代词引用（如"它"、"这个"、"那个"），结合上下文改写为完整问题
3. 返回 JSON 格式

判断标准：
- 相关：医院设计、科室规划、医疗建筑、设计规范等
- 不相关：天气、新闻、编程、娱乐等

返回格式（必须是有效 JSON）：
{
  "is_hospital_related": true/false,
  "rewritten_query": "改写后的完整问题（如果有代词）",
  "general_answer": "如果不相关，给出引导回答"
}
"""
    
    user_prompt = f"""上下文（最近对话）：
{context_str}

当前问题：{query}

请分析并返回 JSON。"""
    
    try:
        llm = await get_orchestrator_llm()
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        
        content = response.content.strip()
        
        # 清理 JSON（移除 markdown 代码块）
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        result = json.loads(content)
        
        is_hospital_related = result.get("is_hospital_related", True)
        rewritten_query = result.get("rewritten_query", query)
        general_answer = result.get("general_answer", "")
        
        logger.info(
            f"[Orchestrator→AnalyzeIntent] 相关性: {is_hospital_related}, "
            f"改写: {rewritten_query if rewritten_query != query else '无'}"
        )
        
        return {
            "is_hospital_related": is_hospital_related,
            "rewritten_query": rewritten_query,
            "general_answer": general_answer,
        }
    
    except Exception as e:
        logger.error(f"[Orchestrator→AnalyzeIntent] LLM 分析失败: {e}，使用启发式")
        # 兜底：启发式判断
        is_related = any(
            keyword in query
            for keyword in ["医院", "设计", "科室", "病房", "门诊", "手术", "ICU", "规范"]
        )
        return {
            "is_hospital_related": is_related,
            "rewritten_query": query,
            "general_answer": "" if is_related else "这个问题不在我的专业领域内。",
        }


async def node_decide_action(state: OrchestratorState) -> Dict[str, Any]:
    """决定下一步动作"""
    is_hospital_related = state.get("is_hospital_related", True)
    rewritten_query = state.get("rewritten_query", "")
    general_answer = state.get("general_answer", "")
    available_workers = state.get("available_workers") or DEFAULT_WORKERS
    
    logger.info(f"[Orchestrator→DecideAction] 相关性: {is_hospital_related}")
    
    # 不相关问题
    if not is_hospital_related:
        final_answer = general_answer or (
            "这个问题不在我的专业领域内。\n\n"
            "💡 我专注于综合医院建筑设计，如果您有相关问题，欢迎咨询！"
        )
        
        return {
            "general_answer": final_answer,
            "agents_to_call": [],
            "diagnostics": {"type": "general_question"},
        }
    
    # 相关问题：选择 Workers
    workers = [w for w in DEFAULT_WORKERS if w in available_workers]
    if not workers:
        workers = list(available_workers) or DEFAULT_WORKERS
    
    logger.info(f"[Orchestrator→DecideAction] 调用 Workers: {workers}")
    
    return {
        "agents_to_call": workers,
        "query": rewritten_query,  # 使用改写后的查询
        "diagnostics": {
            "type": "hospital_related",
            "rewritten": rewritten_query != state.get("extracted_query", ""),
        },
    }


async def node_prepare_request(state: OrchestratorState) -> Dict[str, Any]:
    """准备 AgentRequest"""
    query = state.get("query") or state.get("rewritten_query") or state.get("extracted_query", "")
    
    logger.info(f"[Orchestrator→PrepareRequest] 准备请求: {query}")

    # ✅ 关键：保留上游（API / Supervisor）传入的 filters / top_k / timeout 等参数。
    # 否则会导致 doc scoping（filters.doc_ids/source_documents）在 Orchestrator 阶段被覆盖丢失。
    existing = state.get("request")

    if isinstance(existing, AgentRequest):
        request = AgentRequest(
            query=query,
            filters=existing.filters or {},
            top_k=existing.top_k,
            lang=existing.lang,
            timeout_ms=existing.timeout_ms,
            trace_id=existing.trace_id,
            metadata=existing.metadata or {},
            context=list(existing.context or []),
            attachments=list(existing.attachments or []),
        )
    else:
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
    
    return {"request": request}


# ============================================================================
# 路由函数
# ============================================================================

def route_after_decide(state: OrchestratorState) -> str:
    """决定路由"""
    is_hospital_related = state.get("is_hospital_related", True)
    
    if is_hospital_related:
        return "prepare_request"
    else:
        return END


# ============================================================================
# 构建图
# ============================================================================

def build_orchestrator_graph():
    """构建 Orchestrator 图"""
    builder = StateGraph(OrchestratorState)
    
    # 添加节点
    builder.add_node("extract_query", node_extract_query)
    builder.add_node("analyze_intent", node_analyze_intent)
    builder.add_node("decide_action", node_decide_action)
    builder.add_node("prepare_request", node_prepare_request)
    
    # 设置流程
    builder.set_entry_point("extract_query")
    builder.add_edge("extract_query", "analyze_intent")
    builder.add_edge("analyze_intent", "decide_action")
    
    # 条件路由
    builder.add_conditional_edges(
        "decide_action",
        route_after_decide,
        {
            "prepare_request": "prepare_request",
            END: END,
        }
    )
    
    builder.add_edge("prepare_request", END)
    
    logger.info("[Orchestrator] 图构建完成")
    
    return builder.compile()


# ============================================================================
# 导出图
# ============================================================================

orchestrator_logic_graph = build_orchestrator_graph()
graph = orchestrator_logic_graph

logger.info("[Orchestrator] 图已导出")
