# backend/api/routers/chat.py
"""
对话聊天 API 路由

核心功能:
- POST /api/v1/chat - 发送消息并获取回复
- POST /api/v1/chat/stream - 流式对话（Server-Sent Events）
- GET /api/v1/chat/sessions - 获取会话列表
- GET /api/v1/chat/sessions/{session_id}/history - 获取会话历史
- PATCH /api/v1/chat/sessions/{session_id} - 更新会话
- DELETE /api/v1/chat/sessions/{session_id} - 删除会话
"""

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncGenerator, Dict, Any, List, Optional
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.schemas.chat import (
    ChatRequest,
    ChatResponse,
    StreamingChatChunk,
    SessionListResponse,
    SessionHistoryResponse,
    ChatMessage,
)
from backend.api.schemas.common import Citation
from backend.api.core.config import settings

# 导入 LangGraph Supervisor
from backend.app.agents.supervisor_graph import graph as supervisor_graph
from backend.app.agents.base_agent import AgentRequest, AgentResponse

logger = logging.getLogger("mediarch_api")

router = APIRouter()

# ============================================================================
# 会话管理（简单内存存储，生产环境应使用 Redis/PostgreSQL）
# ============================================================================

# 临时会话存储（进程重启后会丢失）
SESSION_STORE: Dict[str, Dict[str, Any]] = {}


def _get_or_create_session(session_id: str | None = None) -> str:
    """获取或创建会话"""
    if session_id and session_id in SESSION_STORE:
        # 更新最后活跃时间
        SESSION_STORE[session_id]["last_active"] = time.time()
        return session_id

    # 创建新会话
    new_session_id = session_id or f"session-{uuid.uuid4().hex[:16]}"
    SESSION_STORE[new_session_id] = {
        "session_id": new_session_id,
        "created_at": time.time(),
        "last_active": time.time(),
        "messages": [],
        "title": "New Chat",
        "is_pinned": False,
    }
    return new_session_id


def _add_message_to_session(session_id: str, role: str, content: str, citations=None, images=None):
    """添加消息到会话历史"""
    if session_id not in SESSION_STORE:
        return

    message = {
        "role": role,
        "content": content,
        "timestamp": time.time(),
        "citations": citations or [],
        "images": images or [],
    }
    SESSION_STORE[session_id]["messages"].append(message)

    # 自动生成标题（第一条用户消息）
    if role == "user" and SESSION_STORE[session_id]["title"] == "New Chat":
        SESSION_STORE[session_id]["title"] = content[:50] + ("..." if len(content) > 50 else "")

    # 限制历史长度
    max_history = settings.MAX_HISTORY_LENGTH
    if len(SESSION_STORE[session_id]["messages"]) > max_history:
        SESSION_STORE[session_id]["messages"] = SESSION_STORE[session_id]["messages"][-max_history:]


def _extract_citations_from_items(items: List[Any], max_citations: int = 10) -> List[Dict]:
    """
    从检索结果中提取引用信息

    [FIX 2025-12-09] 增强错误处理和日志
    - 确保所有 citations 包含必填字段
    - 记录无效的 citations
    - 使用 normalize_citations 规范化
    """
    from backend.app.utils.citation_builder import normalize_citations

    citations = []
    # 兼顾“每个来源只保留 1 条”与“允许同一来源多张图”：
    # - 普通文本 citation：以 source 去重
    # - 图片 citation：以 (source, image_url) 去重
    seen_sources = set()
    invalid_count = 0

    def _citation_to_dict(cite: Any) -> Dict[str, Any]:
        if cite is None:
            return {}
        if isinstance(cite, dict):
            return {k: v for k, v in cite.items()}
        if hasattr(cite, "model_dump"):
            try:
                return cite.model_dump()
            except Exception:
                pass
        return {
            "source": getattr(cite, 'source', ''),
            "location": getattr(cite, 'location', ''),
            "snippet": getattr(cite, 'snippet', ''),
            "chunk_id": getattr(cite, 'chunk_id', None),
            "page_number": getattr(cite, 'page_number', None),
            "section": getattr(cite, 'section', None),
            "metadata": getattr(cite, 'metadata', None),
            "positions": getattr(cite, 'positions', None),
        }

    def _iter_item_citations(item: Any) -> List[Any]:
        if hasattr(item, "citations") and item.citations:
            return list(item.citations)[:3]
        if isinstance(item, dict) and "citations" in item:
            return list(item.get("citations", []))[:3]
        return []

    def _try_add(data: Dict[str, Any]) -> None:
        source_name = data.get("source", "")
        image_url = data.get("image_url")
        key = (source_name, image_url) if image_url else source_name
        if source_name and key not in seen_sources:
            seen_sources.add(key)
            citations.append(data)

    # Pass 1: 优先收集图片 citations（避免被 max_citations 截断后“有图却不返回图”）
    for item in items:
        if len(citations) >= max_citations:
            break
        for cite in _iter_item_citations(item):
            try:
                data = _citation_to_dict(cite)
                if data.get("image_url"):
                    _try_add(data)
                    if len(citations) >= max_citations:
                        break
            except Exception as e:
                invalid_count += 1
                logger.warning(f"[ExtractCitations] 无效的 citation: {e}, cite={cite}")

    # Pass 2: 再补充文本 citations
    for item in items:
        if len(citations) >= max_citations:
            break
        for cite in _iter_item_citations(item):
            try:
                data = _citation_to_dict(cite)
                if not data.get("image_url"):
                    _try_add(data)
                    if len(citations) >= max_citations:
                        break
            except Exception as e:
                invalid_count += 1
                logger.warning(f"[ExtractCitations] 无效的 citation: {e}, cite={cite}")

    # [FIX 2025-12-09] 规范化 citations，确保必填字段存在
    normalized = normalize_citations(citations)

    if invalid_count > 0:
        logger.warning(f"[ExtractCitations] 跳过了 {invalid_count} 个无效的 citations")

    logger.info(f"[ExtractCitations] 提取了 {len(normalized)} 个有效的 citations")

    return normalized


def _query_path_to_graph(query_path: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not query_path:
        return None

    nodes = []
    links = []
    seen = set()

    for entity in query_path.get("expanded_entities", [])[:20]:
        node_id = entity.get("name") or entity.get("id")
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        nodes.append({
            "id": node_id,
            "label": entity.get("name", node_id),
            "type": entity.get("type", "entity"),
        })

    for rel in query_path.get("expanded_relations", [])[:30]:
        source = rel.get("source")
        target = rel.get("target")
        if not source or not target:
            continue
        links.append({
            "source": source,
            "target": target,
            "label": rel.get("relation", ""),
        })

    return {
        "nodes": nodes,
        "links": links,
        "query_path": query_path,
    }


def _extract_knowledge_graph(worker_responses: List[Dict], result: Dict) -> Optional[Dict]:
    """提取知识图谱数据"""
    # 优先从 answer_graph_data 获取（Synthesizer 生成）
    if "answer_graph_data" in result and result["answer_graph_data"]:
        return result["answer_graph_data"]

    # 从 Neo4j Agent 获取
    for resp in worker_responses:
        if resp.get("agent_name") == "neo4j_agent":
            diag = resp.get("diagnostics", {})
            if "query_path" in diag:
                return _query_path_to_graph(diag["query_path"])

    return None


def _extract_recommended_questions(result: Dict) -> List[str]:
    """提取推荐问题"""
    # 从 Synthesizer 生成的推荐问题
    if "recommended_questions" in result:
        return result["recommended_questions"][:7]

    # 从 final_answer 中尝试提取
    # TODO: 可以用 LLM 生成
    return []


def _to_ocr_image_rel_path(raw: str, ctx: Dict[str, Any]) -> Optional[str]:
    """Normalize image reference to a path relative to backend/databases/documents_ocr."""
    if not raw:
        return None
    path = str(raw).strip().replace("\\", "/").lstrip("/")
    if not path:
        return None

    # Strip known prefixes
    for prefix in ("backend/databases/documents_ocr/", "documents_ocr/"):
        if prefix in path:
            path = path.split(prefix, 1)[1]

    parts = [p for p in path.split("/") if p]
    if not parts or ".." in parts:
        return None

    # Already looks like <category>/<doc>/images/<file>
    if len(parts) >= 4 and parts[2] == "images":
        return "/".join(parts)

    # Legacy: images/<file> (needs category + doc stem)
    if path.startswith("images/"):
        meta = ctx.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        doc_category = ctx.get("doc_category") or meta.get("doc_category") or ctx.get("doc_type") or ""
        source = ctx.get("source") or ctx.get("doc_name") or ctx.get("doc_title") or ""
        doc_stem = Path(str(source)).stem if source else ""
        if doc_category and doc_stem:
            return f"{doc_category}/{doc_stem}/{path}"

    return "/".join(parts)


def _extract_images(citations: List[Dict[str, Any]], api_base: str, max_images: int = 5) -> List[str]:
    """Build browser-accessible image URLs from citations."""
    api_base = (api_base or "").rstrip("/")
    api_root = f"{api_base}{settings.API_PREFIX}".rstrip("/")
    images: List[str] = []
    seen: set[str] = set()

    for cite in citations or []:
        raw = cite.get("image_url") or ""
        raw = str(raw).strip()
        if not raw:
            continue

        if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("data:"):
            url = raw
        elif raw.startswith("/api/"):
            url = f"{api_base}{raw}"
        elif raw.startswith("/documents/image"):
            url = f"{api_root}{raw}"
        else:
            rel = _to_ocr_image_rel_path(raw, cite)
            if not rel:
                continue
            url = f"{api_root}/documents/image?path={quote(rel)}"

        if url in seen:
            continue
        seen.add(url)
        images.append(url)
        if len(images) >= max_images:
            break

    return images


def _image_placeholder_block(images: List[str]) -> str:
    if not images:
        return ""
    placeholders = "\n".join(f"[image:{i}]" for i in range(len(images)))
    return "\n\n### 相关图示\n" + placeholders


def _append_image_placeholders(answer: str, images: List[str]) -> str:
    if not images:
        return answer
    if "[image:" in (answer or ""):
        return answer
    return (answer or "").rstrip() + _image_placeholder_block(images)

# ============================================================================
# 智能体状态推送
# ============================================================================

class AgentStatusUpdate(BaseModel):
    """智能体状态更新"""
    agent_name: str
    status: str  # pending, running, completed, error
    thought: Optional[str] = None
    progress: Optional[float] = None
    took_ms: Optional[int] = None


def _create_agent_status_chunk(agent_name: str, status: str, thought: str = None) -> str:
    """创建智能体状态 SSE 块"""
    chunk = StreamingChatChunk(
        chunk_type="agent_status",
        agent_status={
            "agent_name": agent_name,
            "status": status,
            "thought": thought,
        },
        is_final=False
    )
    return f"data: {chunk.model_dump_json()}\n\n"


# ============================================================================
# 会话更新请求模型
# ============================================================================

class SessionUpdateRequest(BaseModel):
    """会话更新请求"""
    title: Optional[str] = Field(None, max_length=100, description="会话标题")
    is_pinned: Optional[bool] = Field(None, description="是否置顶")


# ============================================================================
# API 端点
# ============================================================================


@router.post("/chat", response_model=ChatResponse, summary="对话接口（非流式）")
async def chat(http_request: Request, request: ChatRequest):
    """
    发送消息并获取完整回复（非流式）

    Args:
        request: 对话请求

    Returns:
        完整的对话响应
    """
    start_time = time.time()

    try:
        # 创建或获取会话
        session_id = _get_or_create_session(request.session_id)
        logger.info(f"[Chat] 收到消息: {request.message[:50]}... | Session: {session_id}")

        # 添加用户消息到历史
        _add_message_to_session(session_id, "user", request.message)

        # 构建 AgentRequest
        agent_request = AgentRequest(
            query=request.message,
            filters=request.filters or {},
            top_k=request.top_k or 8,
            timeout_ms=settings.SUPERVISOR_TIMEOUT_MS,
            metadata={
                "session_id": session_id,
                "include_online_search": request.include_online_search,
                "original_query": request.message,
            }
        )

        # 调用 LangGraph Supervisor
        config = {"configurable": {"thread_id": session_id}}
        result = await supervisor_graph.ainvoke(
            {"request": agent_request, "original_query": request.message},
            config=config
        )

        # 提取响应
        final_answer = result.get("final_answer", "抱歉，我无法回答这个问题。")
        items = result.get("items", [])
        diagnostics_list = result.get("diagnostics", {})
        worker_responses = result.get("worker_responses", [])
        strict_cross_doc_mode = bool(result.get("strict_cross_doc"))

        api_base = str(http_request.base_url).rstrip('/')

        # [DEBUG/QA] 透传严格交叉验证状态，便于回归
        try:
            if not isinstance(diagnostics_list, dict):
                diagnostics_list = {"raw": diagnostics_list}
            diagnostics_list.setdefault("additional_info", {})
            if isinstance(diagnostics_list.get("additional_info"), dict):
                diagnostics_list["additional_info"]["result_has_strict_cross_doc"] = "strict_cross_doc" in result
                diagnostics_list["additional_info"]["result_has_final_citations"] = "final_citations" in result
                diagnostics_list["additional_info"]["result_has_strict_citations_candidate_count"] = (
                    "strict_citations_candidate_count" in result
                )
                try:
                    diagnostics_list["additional_info"]["agents_used"] = [
                        resp.get("agent_name") for resp in (worker_responses or []) if isinstance(resp, dict)
                    ]
                except Exception:
                    diagnostics_list["additional_info"]["agents_used"] = []
                diagnostics_list["additional_info"]["strict_cross_doc"] = strict_cross_doc_mode
                fc = result.get("final_citations")
                diagnostics_list["additional_info"]["final_citations_count"] = len(fc) if isinstance(fc, list) else 0
                try:
                    diagnostics_list["additional_info"]["strict_citations_candidate_count"] = int(
                        result.get("strict_citations_candidate_count") or 0
                    )
                except Exception:
                    diagnostics_list["additional_info"]["strict_citations_candidate_count"] = 0
            # 观察最终 state.request.filters 是否丢失（排查 strict_cross_doc 未触发）
            state_req = result.get("request")
            state_filters = {}
            if hasattr(state_req, "filters"):
                try:
                    state_filters = getattr(state_req, "filters") or {}
                except Exception:
                    state_filters = {}
            elif isinstance(state_req, dict):
                state_filters = state_req.get("filters") or {}
            if isinstance(state_filters, dict):
                diagnostics_list["additional_info"]["state_filters_keys"] = sorted(state_filters.keys())
                doc_ids = state_filters.get("doc_ids") or state_filters.get("doc_id") or []
                diagnostics_list["additional_info"]["state_doc_ids_count"] = len(doc_ids) if isinstance(doc_ids, list) else (1 if doc_ids else 0)
            diagnostics_list["additional_info"]["state_strict_cross_doc_request"] = bool(result.get("strict_cross_doc_request"))
        except Exception:
            pass

        # 提取各类信息
        max_citations = int(getattr(request, "max_citations", None) or 10)
        # 优先使用 Synthesizer 透传的最终 citations（用于严格交叉验证对齐 [n]）
        final_citations_override = result.get("final_citations")
        if isinstance(final_citations_override, list) and final_citations_override:
            citations_full = final_citations_override
        else:
            citations_full = _extract_citations_from_items(items, max_citations=max_citations)
        citations = citations_full if request.include_citations else []
        kg_data = _extract_knowledge_graph(worker_responses, result)
        recommended_questions = _extract_recommended_questions(result)
        images = _extract_images(citations_full, api_base)

        # 让前端能渲染图片：在文本末尾追加 [image:n] 占位符
        # 严格交叉验证模式下，为避免破坏“只输出三段标题”的格式，不追加占位符。
        if not strict_cross_doc_mode:
            final_answer = _append_image_placeholders(final_answer, images)

        # 添加助手回复到历史
        _add_message_to_session(session_id, "assistant", final_answer, citations, images)

        # 计算总耗时
        took_ms = int((time.time() - start_time) * 1000)

        # 构建响应
        response = ChatResponse(
            message=final_answer,
            session_id=session_id,
            knowledge_graph_path=kg_data,
            citations=citations,
            recommended_questions=recommended_questions,
            diagnostics=[] if not request.include_diagnostics else [diagnostics_list],
            took_ms=took_ms,
            agents_used=[resp.get("agent_name") for resp in worker_responses],
            images=images,
        )

        logger.info(f"[Chat] 回复成功 | Session: {session_id} | Time: {took_ms}ms")
        return response

    except Exception as e:
        logger.exception(f"[Chat] 处理失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"对话处理失败: {str(e)}"
        )


@router.post("/chat/stream", summary="对话接口（流式）")
async def chat_stream(http_request: Request, request: ChatRequest):
    """
    发送消息并获取流式回复（Server-Sent Events）

    支持的事件类型:
    - session: 会话ID
    - agent_status: 智能体状态更新
    - content: 回复内容片段
    - citations: 引用信息
    - knowledge_graph: 知识图谱数据
    - recommendations: 推荐问题
    - images: 相关图片
    - done: 完成信号
    - error: 错误信息

    Args:
        request: 对话请求

    Returns:
        StreamingResponse with text/event-stream
    """
    api_base = str(http_request.base_url).rstrip('/')

    async def generate() -> AsyncGenerator[str, None]:
        """生成 SSE 事件流"""
        session_id = None
        start_time = time.time()

        try:
            # 创建或获取会话
            session_id = _get_or_create_session(request.session_id)
            logger.info(f"[Chat Stream] 收到消息: {request.message[:50]}... | Session: {session_id}")

            # 发送会话ID
            chunk = StreamingChatChunk(
                chunk_type="session",
                content=session_id,
                is_final=False
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

            # 添加用户消息到历史
            _add_message_to_session(session_id, "user", request.message)

            # 发送智能体开始状态
            agents = ["Orchestrator", "Neo4j", "Milvus", "MongoDB", "OnlineSearch", "Synthesizer"]
            yield _create_agent_status_chunk("Orchestrator", "running", "Analyzing query...")

            # 构建 AgentRequest
            agent_request = AgentRequest(
                query=request.message,
                filters=request.filters or {},
                top_k=request.top_k or 8,
                timeout_ms=settings.SUPERVISOR_TIMEOUT_MS,
                metadata={
                    "session_id": session_id,
                    "include_online_search": request.include_online_search,
                    "original_query": request.message,
                }
            )

            # 调用 LangGraph Supervisor（使用 astream 获取中间状态）
            config = {"configurable": {"thread_id": session_id}}

            # 流式返回中间状态
            final_answer = ""
            all_items = []
            all_citations = []
            kg_data = None
            recommended_questions = []
            images = []
            worker_responses = []
            final_citations_override = []
            neo4j_graph_sent = False

            # 追踪已发送状态的Agent，避免重复发送
            agent_status_sent = {
                "Orchestrator": False,
                "Neo4j": False,
                "Milvus": False,
                "MongoDB": False,
                "Synthesizer": False,
            }
            agent_completed = {
                "Orchestrator": False,
                "Neo4j": False,
                "Milvus": False,
                "MongoDB": False,
                "Synthesizer": False,
            }

            async for event in supervisor_graph.astream(
                {"request": agent_request, "original_query": request.message},
                config=config
            ):
                # 发送中间进度
                if isinstance(event, dict):
                    # LangGraph astream 返回的 event 格式: {node_name: node_output}
                    # 例如: {"orchestrator_agent": {...}}, {"neo4j_agent": {...}}
                    for node_name, node_output in event.items():
                        thought = None

                        # 从节点输出中提取思考信息
                        if isinstance(node_output, dict):
                            # 尝试从 diagnostics 中提取信息
                            diag = node_output.get("diagnostics", {})
                            if isinstance(diag, dict):
                                thought = diag.get("reasoning") or diag.get("thought") or diag.get("analysis_reasoning")

                            # 从 worker_responses 中提取
                            if "worker_responses" in node_output:
                                for wr in node_output["worker_responses"]:
                                    if isinstance(wr, dict):
                                        wr_diag = wr.get("diagnostics", {})
                                        if isinstance(wr_diag, dict) and not thought:
                                            thought = wr_diag.get("reasoning") or wr_diag.get("thought")

                        # 根据节点名称发送状态更新
                        if node_name == "orchestrator_agent":
                            if not agent_status_sent["Orchestrator"]:
                                yield _create_agent_status_chunk("Orchestrator", "completed", thought or "Query analysis completed")
                                agent_completed["Orchestrator"] = True
                                agent_status_sent["Orchestrator"] = True

                        elif node_name == "neo4j_agent":
                            diagnostics = node_output.get("diagnostics", {}) if isinstance(node_output, dict) else {}
                            if not agent_status_sent["Neo4j"]:
                                yield _create_agent_status_chunk("Neo4j", "running", "Querying knowledge graph...")
                                agent_status_sent["Neo4j"] = True
                            # Neo4j 完成
                            if isinstance(node_output, dict) and ("items" in node_output or "diagnostics" in node_output):
                                yield _create_agent_status_chunk("Neo4j", "completed", thought or "Knowledge graph query completed")
                                agent_completed["Neo4j"] = True
                                query_path = diagnostics.get("query_path")
                                if query_path and not neo4j_graph_sent:
                                    kg_payload = _query_path_to_graph(query_path)
                                    if kg_payload:
                                        kg_data = kg_payload
                                        chunk = StreamingChatChunk(
                                            chunk_type="knowledge_graph",
                                            knowledge_graph_path=kg_payload,
                                            is_final=False
                                        )
                                        yield f"data: {chunk.model_dump_json()}\n\n"
                                        neo4j_graph_sent = True

                        elif node_name == "milvus_agent":
                            if not agent_status_sent["Milvus"]:
                                yield _create_agent_status_chunk("Milvus", "running", "Vector similarity search...")
                                agent_status_sent["Milvus"] = True
                            # Milvus 完成
                            if isinstance(node_output, dict) and ("items" in node_output or "diagnostics" in node_output):
                                yield _create_agent_status_chunk("Milvus", "completed", thought or "Vector search completed")
                                agent_completed["Milvus"] = True

                        elif node_name == "mongodb_agent":
                            if not agent_status_sent["MongoDB"]:
                                yield _create_agent_status_chunk("MongoDB", "running", "Document retrieval...")
                                agent_status_sent["MongoDB"] = True
                            # MongoDB 完成
                            if isinstance(node_output, dict) and ("items" in node_output or "diagnostics" in node_output):
                                yield _create_agent_status_chunk("MongoDB", "completed", thought or "Document retrieval completed")
                                agent_completed["MongoDB"] = True

                        elif node_name == "result_synthesizer_agent":
                            if not agent_status_sent["Synthesizer"]:
                                yield _create_agent_status_chunk("Synthesizer", "running", "Generating comprehensive answer...")
                                agent_status_sent["Synthesizer"] = True

                        elif node_name == "knowledge_fusion":
                            # Knowledge Fusion 完成时，说明 Neo4j 和 Milvus 阶段1都已完成
                            if not agent_completed["Neo4j"]:
                                yield _create_agent_status_chunk("Neo4j", "completed", "Knowledge graph query completed")
                                agent_completed["Neo4j"] = True
                            if not agent_completed["Milvus"]:
                                yield _create_agent_status_chunk("Milvus", "completed", "Vector search completed")
                                agent_completed["Milvus"] = True

                        elif node_name == "prepare_parallel_workers":
                            # 准备阶段完成，Orchestrator 已完成
                            if not agent_completed["Orchestrator"]:
                                yield _create_agent_status_chunk("Orchestrator", "completed", thought or "Query analysis completed")
                                agent_completed["Orchestrator"] = True

                        elif node_name == "fan_out_workers":
                            # 开始并行检索
                            if not agent_status_sent["Neo4j"]:
                                yield _create_agent_status_chunk("Neo4j", "running", "Querying knowledge graph...")
                                agent_status_sent["Neo4j"] = True
                            if not agent_status_sent["Milvus"]:
                                yield _create_agent_status_chunk("Milvus", "running", "Vector similarity search...")
                                agent_status_sent["Milvus"] = True

                    # 收集中间结果
                    for node_name, node_output in event.items():
                        if isinstance(node_output, dict):
                            if "items" in node_output:
                                items_data = node_output["items"]
                                if isinstance(items_data, list):
                                    all_items.extend(items_data)

                    # 从各节点输出中收集worker_responses和final_answer
                    for node_name, node_output in event.items():
                        if isinstance(node_output, dict):
                            # 收集 worker_responses
                            if "worker_responses" in node_output:
                                wr_list = node_output["worker_responses"]
                                if isinstance(wr_list, list):
                                    worker_responses.extend(wr_list)

                            # 检查 final_answer（通常来自 result_synthesizer_agent 或 push_answer_message）
                            if "final_answer" in node_output and node_output["final_answer"]:
                                yield _create_agent_status_chunk("Synthesizer", "completed", "Answer generation completed")
                                final_answer = node_output["final_answer"]

                                # 逐字符流式发送答案
                                for i in range(0, len(final_answer), 10):  # 每次发送10个字符
                                    chunk_text = final_answer[i:i+10]
                                    chunk = StreamingChatChunk(
                                        chunk_type="content",
                                        content=chunk_text,
                                        is_final=False
                                    )
                                    yield f"data: {chunk.model_dump_json()}\n\n"
                                    await asyncio.sleep(0.02)  # 模拟打字延迟

                            # 提取知识图谱
                            if "answer_graph_data" in node_output and node_output["answer_graph_data"]:
                                kg_data = node_output["answer_graph_data"]

                            # 提取推荐问题
                            if "recommended_questions" in node_output and node_output["recommended_questions"]:
                                recommended_questions = node_output["recommended_questions"]

                            # 提取 Synthesizer 最终 citations（用于严格交叉验证）
                            if "final_citations" in node_output and node_output["final_citations"]:
                                fc = node_output["final_citations"]
                                if isinstance(fc, list):
                                    final_citations_override = fc

                            # 提取 unified_hints 中的知识图谱数据
                            if "unified_hints" in node_output and node_output["unified_hints"]:
                                unified_hints = node_output["unified_hints"]
                                # unified_hints 也可以用于构建知识图谱
                                if not kg_data and unified_hints.get("entity_names"):
                                    kg_data = {
                                        "nodes": [{"id": name, "label": name, "type": "entity"}
                                                  for name in unified_hints.get("entity_names", [])[:15]],
                                        "links": [],
                                        "unified_hints": unified_hints,
                                    }

            # 提取引用信息（图片也依赖 citations 来构建 URL）
            max_citations = int(getattr(request, "max_citations", None) or 10)
            all_citations_full = final_citations_override if final_citations_override else _extract_citations_from_items(all_items, max_citations=max_citations)
            all_citations = all_citations_full if request.include_citations else []

            # 提取知识图谱（如果还没有）
            if not kg_data:
                kg_data = _extract_knowledge_graph(worker_responses, {"answer_graph_data": None})

            # 提取图片（构建浏览器可访问的 /api/v1/documents/image?... URL）
            images = _extract_images(all_citations_full, api_base)

            # 提取推荐问题（如果还没有）
            if not recommended_questions:
                recommended_questions = _extract_recommended_questions({"recommended_questions": []})

            # 发送引用信息
            if all_citations:
                try:
                    # [FIX 2025-12-09] 添加 citations 验证和规范化
                    from backend.app.utils.citation_builder import normalize_citations
                    normalized_citations = normalize_citations(all_citations)

                    chunk = StreamingChatChunk(
                        chunk_type="citations",
                        citations=normalized_citations,
                        is_final=False
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"
                except Exception as e:
                    logger.error(f"[Chat Stream] 发送 citations 失败: {e}, citations={all_citations[:2]}...")
                    # 不中断流式响应，继续发送其他数据

            # 发送知识图谱数据
            if kg_data:
                chunk = StreamingChatChunk(
                    chunk_type="knowledge_graph",
                    knowledge_graph_path=kg_data,
                    is_final=False
                )
                yield f"data: {chunk.model_dump_json()}\n\n"

            # 发送推荐问题
            if recommended_questions:
                chunk = StreamingChatChunk(
                    chunk_type="recommendations",
                    recommended_questions=recommended_questions,
                    is_final=False
                )
                yield f"data: {chunk.model_dump_json()}\n\n"

            # 发送图片
            if images:
                chunk = StreamingChatChunk(
                    chunk_type="images",
                    images=images,
                    is_final=False
                )
                yield f"data: {chunk.model_dump_json()}\n\n"

            # 让前端能渲染图片：追加 [image:n] 占位符块
            if images and '[image:' not in (final_answer or ''):
                placeholder_block = _image_placeholder_block(images)
                if placeholder_block:
                    chunk = StreamingChatChunk(
                        chunk_type='content',
                        content=placeholder_block,
                        is_final=False,
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

            # 历史记录也存储带占位符的最终答案
            final_answer = _append_image_placeholders(final_answer, images)

            # 添加助手回复到历史
            _add_message_to_session(session_id, "assistant", final_answer, all_citations, images)

            # 发送完成信号
            took_ms = int((time.time() - start_time) * 1000)
            chunk = StreamingChatChunk(
                chunk_type="done",
                content=json.dumps({"took_ms": took_ms}),
                is_final=True
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

            logger.info(f"[Chat Stream] 回复完成 | Session: {session_id} | Time: {took_ms}ms")

        except Exception as e:
            logger.exception(f"[Chat Stream] 流式处理失败: {e}")
            error_chunk = StreamingChatChunk(
                chunk_type="error",
                content=f"处理失败: {str(e)}",
                is_final=True
            )
            yield f"data: {error_chunk.model_dump_json()}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        }
    )


@router.get("/chat/sessions", response_model=SessionListResponse, summary="获取会话列表")
async def list_sessions():
    """获取所有会话列表"""
    try:
        sessions = [
            {
                "session_id": session_data["session_id"],
                "created_at": session_data["created_at"],
                "last_active": session_data["last_active"],
                "message_count": len(session_data["messages"]),
                "title": session_data.get("title", "New Chat"),
                "is_pinned": session_data.get("is_pinned", False),
            }
            for session_data in SESSION_STORE.values()
        ]

        # 排序：置顶在前，然后按最后活跃时间降序
        sessions.sort(key=lambda x: (-int(x.get("is_pinned", False)), -x["last_active"]))

        return SessionListResponse(
            sessions=sessions,
            total=len(sessions)
        )

    except Exception as e:
        logger.exception(f"[Sessions] 获取会话列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取会话列表失败"
        )


@router.get("/chat/sessions/{session_id}/history", response_model=SessionHistoryResponse, summary="获取会话历史")
async def get_session_history(session_id: str):
    """获取指定会话的对话历史"""
    try:
        if session_id not in SESSION_STORE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"会话 {session_id} 不存在"
            )

        session_data = SESSION_STORE[session_id]
        # 兼容历史数据：对 citations 做一次规范化（字段命名/路径前缀等）
        from backend.app.utils.citation_builder import normalize_citations

        messages = []
        for msg in session_data["messages"]:
            msg_copy = dict(msg) if isinstance(msg, dict) else {"role": "system", "content": str(msg)}
            if isinstance(msg_copy.get("citations"), list):
                msg_copy["citations"] = normalize_citations(msg_copy["citations"])
            messages.append(ChatMessage(**msg_copy))

        return SessionHistoryResponse(
            session_id=session_id,
            messages=messages,
            total=len(messages)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Session History] 获取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取会话历史失败"
        )


@router.patch("/chat/sessions/{session_id}", summary="更新会话")
async def update_session(session_id: str, update_data: SessionUpdateRequest):
    """更新会话信息（标题、置顶状态等）"""
    try:
        if session_id not in SESSION_STORE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"会话 {session_id} 不存在"
            )

        session = SESSION_STORE[session_id]

        if update_data.title is not None:
            session["title"] = update_data.title

        if update_data.is_pinned is not None:
            session["is_pinned"] = update_data.is_pinned

        session["last_active"] = time.time()

        logger.info(f"[Session] 更新成功: {session_id}")

        return {
            "message": "会话已更新",
            "session_id": session_id,
            "title": session.get("title"),
            "is_pinned": session.get("is_pinned"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Update Session] 更新失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新会话失败"
        )


@router.delete("/chat/sessions/{session_id}", summary="删除会话")
async def delete_session(session_id: str):
    """删除指定会话"""
    try:
        if session_id not in SESSION_STORE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"会话 {session_id} 不存在"
            )

        del SESSION_STORE[session_id]
        logger.info(f"[Session] 删除成功: {session_id}")

        return {"message": "会话已删除", "session_id": session_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Delete Session] 删除失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除会话失败"
        )
