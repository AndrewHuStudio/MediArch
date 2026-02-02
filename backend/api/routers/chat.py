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

# 导入 LangGraph MediArch Graph
from backend.app.agents.mediarch_graph import graph as mediarch_graph
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


def _extract_citations_from_items(
    items: List[Any],
    max_citations: int = 10,
    *,
    allow_images: bool = True,
) -> List[Dict]:
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
        if image_url and not allow_images:
            return
        key = (source_name, image_url) if image_url else source_name
        if source_name and key not in seen_sources:
            seen_sources.add(key)
            citations.append(data)

    # Pass 1: 视需求决定是否优先收集图片 citations
    if allow_images:
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


def _postprocess_answer_and_align_citations(
    answer: str,
    citations_full: List[Any],
    *,
    include_citations: bool,
) -> tuple[str, List[Any]]:
    """
    API 层最终兜底：强制对齐“正文引用标记”与“返回的 citations 列表”。

    解决的问题：
    - 正文出现越界引用（例如 sources 只有 4 条，但正文仍出现 [8][9]）
    - 正文第一处引用与侧边 PDF/参考资料列表不对应（编号不符合阅读直觉）
    - 引用过于频繁（每句/每分点后面都有）：压缩到段落/列表块末尾一次
    """
    import re

    if not answer:
        return answer, citations_full

    # 若不返回 citations，则移除正文中的 `[n]`，避免出现“悬空引用”
    if (not include_citations) or (not citations_full):
        cleaned = re.sub(r"\[(\d+)\]", "", answer)
        cleaned = re.sub(r" +", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip(), citations_full

    # 复用 Synthesizer 的后处理逻辑，确保“正文/侧边徽标/参考资料列表”三端一致
    from backend.app.agents.result_synthesizer_agent.agent import (
        _normalize_inline_citation_groups,
        _tighten_citation_spacing,
        _remap_citations_by_first_appearance,
        _sort_adjacent_citation_groups,
        _expand_citation_ranges,
        _strip_decorative_symbols,
        _split_heading_lines,
        _relocate_heading_citations,
        _strip_citations_in_tables,
        _relocate_leading_citations,
    )

    citations_count = len(citations_full)

    # 文本清洗：避免 `\n[1]` 被前端误判为新段落/列表
    cleaned = re.sub(r"\n+\s*(\[\d+\])", r"\1", answer)
    cleaned = re.sub(r"\+\d+", "", cleaned)
    cleaned = cleaned.replace("🔗", "")
    cleaned = re.sub(r" +", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    cleaned = _strip_decorative_symbols(cleaned)
    cleaned = _split_heading_lines(cleaned)
    cleaned = _normalize_inline_citation_groups(cleaned)
    cleaned = _expand_citation_ranges(cleaned)
    cleaned = _relocate_heading_citations(cleaned)
    cleaned = _strip_citations_in_tables(cleaned)
    cleaned = _relocate_leading_citations(cleaned)
    cleaned = _tighten_citation_spacing(cleaned)

    # 重新编号：按“正文首次出现顺序”从 [1] 开始，并同步重排 citations（也会移除越界引用）
    cleaned, citations_full = _remap_citations_by_first_appearance(cleaned, citations_full)
    cleaned = _sort_adjacent_citation_groups(cleaned)
    cleaned = _tighten_citation_spacing(cleaned)

    return cleaned.strip(), citations_full


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


def _infer_image_role(text: str) -> str:
    t = str(text or "")
    if any(k in t for k in ("总平面", "平面", "总体", "总图", "布置", "布局")):
        return "overall"
    if any(k in t for k in ("详图", "节点", "构造", "剖面", "大样")):
        return "detail"
    if any(k in t for k in ("接口", "管线", "给排水", "电气", "弱电", "强电", "风管", "管道")):
        return "interface"
    return "other"


_IMAGE_QUERY_TRIGGERS = (
    "平面图",
    "剖面图",
    "立面图",
    "总平面",
    "图纸",
    "图示",
    "示意图",
    "流程图",
    "结构图",
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
)

_IMAGE_QUERY_NEGATIONS = (
    "不要图",
    "不需要图",
    "不看图",
    "不要图片",
    "不需要图片",
)


def _wants_images_in_answer(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    if any(neg in q for neg in _IMAGE_QUERY_NEGATIONS):
        return False
    return True


def _build_image_caption(cite: Dict[str, Any]) -> str:
    import re

    source = str(cite.get("doc_name") or cite.get("source") or "").strip()
    page = cite.get("page_number")
    location = str(cite.get("location") or "").strip()
    snippet = str(cite.get("snippet") or "").strip()

    # 优先用 snippet 的“图片说明/标题”部分
    desc = snippet
    desc = desc.replace("[图片:", "").replace("[图片：", "").replace("[图片", "").replace("]", "").strip()
    desc = re.sub(r"\s+", " ", desc)
    if len(desc) > 70:
        desc = desc[:69] + "…"

    if not desc:
        desc = location or "相关配图"

    head = source or "资料配图"
    if isinstance(page, int) and page > 0:
        head = f"{head}（第{page}页）"
    return f"{head}：{desc}"


def _order_image_refs_for_injection(image_refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    role_order = {"overall": 0, "detail": 1, "interface": 2, "other": 3}

    def _key(ref: Dict[str, Any]) -> tuple[int, int]:
        role = str(ref.get("role") or "other")
        order = role_order.get(role, 3)
        page = ref.get("page_number")
        try:
            page_num = int(page) if page is not None else 10**6
        except Exception:
            page_num = 10**6
        return (order, page_num)

    return sorted(image_refs, key=_key)


def _extract_image_refs(citations: List[Dict[str, Any]], api_base: str, max_images: int = 5) -> List[Dict[str, Any]]:
    """Build browser-accessible image URLs + captions from citations."""
    api_base = (api_base or "").rstrip("/")
    api_root = f"{api_base}{settings.API_PREFIX}".rstrip("/")
    out: List[Dict[str, Any]] = []
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

        caption = _build_image_caption(cite)
        out.append(
            {
                "url": url,
                "caption": caption,
                "role": _infer_image_role(caption),
                "source": cite.get("source") or cite.get("doc_name"),
                "page_number": cite.get("page_number"),
            }
        )
        if len(out) >= max_images:
            break

    return out


def _build_image_relation_summary(image_refs: List[Dict[str, Any]]) -> str:
    roles = [str(r.get("role") or "other") for r in image_refs]
    has_overall = "overall" in roles
    has_detail = "detail" in roles
    has_interface = "interface" in roles

    if has_overall and has_detail and has_interface:
        return "建议按“整体布置 → 局部节点 → 机电接口”顺序阅读：先看平面/布局，再看关键节点详图，最后核对接口与系统需求。"
    if has_overall and has_detail:
        return "图示呈“整体—局部”关系：先看平面/布局把握尺度与分区，再看节点详图确认关键设备与构造做法。"
    if has_overall and has_interface:
        return "图示可按“空间布置 → 系统接口”阅读：先确定平面/流线，再核对给排水、电气、净化等接口条件。"
    if has_detail and has_interface:
        return "图示偏“节点—接口”关系：先看节点详图，再对照接口/管线条件，确保落地施工可实现。"
    return ""


def _image_placeholder_block(image_refs: List[Dict[str, Any]]) -> str:
    if not image_refs:
        return ""

    lines: List[str] = []
    relation = _build_image_relation_summary(image_refs)
    if relation:
        lines.append("\n\n【图示关系】" + relation)

    for i, ref in enumerate(image_refs):
        caption = str(ref.get("caption") or "").strip() or "相关配图"
        lines.append(f"\n\n（图{i+1}：{caption}）\n[image:{i}]")

    return "".join(lines)


def _append_image_placeholders(answer: str, image_refs: List[Dict[str, Any]]) -> str:
    if not image_refs:
        return answer
    if "[image:" in (answer or ""):
        return answer
    return (answer or "").rstrip() + _image_placeholder_block(image_refs)


def _inject_image_placeholders_inline(answer: str, image_refs: List[Dict[str, Any]]) -> str:
    """Try to interleave images into the main body instead of appending a block at the end.

    - If answer already contains `[image:` tokens, keep it as-is (do NOT reorder images).
    - Otherwise, inject captioned `[image:i]` after paragraphs in the most relevant section (prefer `### 资料印证`).
    """
    if not image_refs:
        return answer
    text = (answer or "").rstrip()
    if "[image:" in text:
        return text

    import re

    # Keep the citation catalog section intact (avoid injecting images into the reference list).
    head = text
    tail = ""
    m_ref = re.search(r"(?m)^###\s*引用\s*$", text)
    if m_ref:
        head = text[: m_ref.start()].rstrip()
        tail = "\n\n" + text[m_ref.start() :].lstrip()

    # Prefer inserting images into "资料印证" section, then "关键洞察", then "开场概览".
    preferred_headers = ("资料印证", "关键洞察", "开场概览")
    insert_at = 0
    section_end = len(head)

    def _find_section(name: str) -> Optional[tuple[int, int]]:
        m = re.search(rf"(?m)^###\s*{re.escape(name)}\s*$", head)
        if not m:
            return None
        start = m.end()
        m_next = re.search(r"(?m)^###\s+", head[start:])
        end = start + (m_next.start() if m_next else len(head[start:]))
        nl = head.find("\n", start)
        body_start = (nl + 1) if nl != -1 and nl < end else start
        return body_start, end

    for name in preferred_headers:
        sec = _find_section(name)
        if sec:
            insert_at, section_end = sec
            break

    # Fallback: insert after the first paragraph.
    if insert_at == 0 and section_end == len(head):
        first_break = head.find("\n\n")
        insert_at = (first_break + 2) if first_break != -1 else len(head)
        section_end = len(head)

    prefix = head[:insert_at]
    target = head[insert_at:section_end]
    suffix = head[section_end:]

    paragraphs = [p for p in re.split(r"\n{2,}", target) if p.strip() != ""]
    if not paragraphs:
        return (prefix.rstrip() + _image_placeholder_block(image_refs) + "\n\n" + (target + suffix).lstrip()).rstrip() + tail

    relation = _build_image_relation_summary(image_refs)

    for i in range(len(image_refs)):
        idx = min(i, len(paragraphs) - 1)
        caption = str(image_refs[i].get("caption") or "").strip() or "相关配图"
        token = f"\n\n（图{i+1}：{caption}）\n[image:{i}]"
        if i == 0 and relation:
            token = f"\n\n【图示关系】{relation}" + token
        paragraphs[idx] = paragraphs[idx].rstrip() + token

    rebuilt = "\n\n".join(paragraphs)
    return (prefix + rebuilt + suffix).rstrip() + tail

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

# 智能体节点中文映射（用于前端思考过程展示）
AGENT_NODE_LABELS = {
    "orchestrator": {
        "extract_query": "提取用户问题",
        "analyze_intent": "意图分析与相关性判断",
        "decide_action": "决定调用哪些智能体",
        "prepare_request": "组装检索请求与参数",
    },
    "neo4j": {
        "init_params": "初始化图谱检索参数",
        "query_analysis": "查询解析与关键词提取",
        "entity_match": "实体匹配",
        "relation_reasoning": "关系推理与路径扩展",
        "community_filter": "子图/社区筛选",
        "merge_results": "合并图谱结果",
        "reflection": "质量评估与是否重试",
        "add_citations": "添加规范引用",
    },
    "milvus": {
        "extract_query": "提取检索问题",
        "rewrite_query": "查询改写",
        "search": "向量检索",
        "extract_knowledge": "提取知识点",
        "format": "结果格式化",
    },
    "mongodb": {
        "extract_query": "提取检索问题",
        "rewrite_query": "查询改写",
        "search": "文档检索与过滤",
        "format": "结果格式化",
    },
    "online_search": {
        "extract_query": "提取查询",
        "search": "在线搜索",
        "format": "结果整理",
    },
    "synthesizer": {
        "aggregate": "汇总多源结果",
        "synthesize": "生成最终答案",
    },
}

AGENT_NAMESPACE_KEYS = {
    "orchestrator_agent": "orchestrator",
    "neo4j_agent": "neo4j",
    "milvus_agent": "milvus",
    "mongodb_agent": "mongodb",
    "online_search_agent": "online_search",
    "result_synthesizer_agent": "synthesizer",
}

AGENT_DISPLAY_NAMES = {
    "orchestrator": "Orchestrator",
    "neo4j": "Neo4j",
    "milvus": "Milvus",
    "mongodb": "MongoDB",
    "online_search": "OnlineSearch",
    "synthesizer": "Synthesizer",
}


def _normalize_stream_event(event: Any) -> List[tuple[tuple[str, ...], Dict[str, Any]]]:
    """标准化 LangGraph stream 事件为 (namespace, payload) 列表。"""
    normalized: List[tuple[tuple[str, ...], Dict[str, Any]]] = []
    if isinstance(event, dict):
        normalized.append(((), event))
        return normalized

    if isinstance(event, tuple):
        if len(event) == 2:
            ns, payload = event
            if isinstance(payload, dict):
                normalized.append((tuple(ns) if isinstance(ns, (list, tuple)) else (), payload))
        elif len(event) == 3:
            ns, mode, payload = event
            if mode == "updates" and isinstance(payload, dict):
                normalized.append((tuple(ns) if isinstance(ns, (list, tuple)) else (), payload))

    return normalized


def _extract_agent_key(namespace: tuple[str, ...], node_name: str) -> Optional[str]:
    for part in reversed(namespace or ()):
        name = str(part).split(":", 1)[0]
        if name in AGENT_NAMESPACE_KEYS:
            return AGENT_NAMESPACE_KEYS[name]
    if node_name in AGENT_NAMESPACE_KEYS:
        return AGENT_NAMESPACE_KEYS[node_name]
    return None


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
        default_max_citations = 15 if request.deep_search else 9
        max_citations_setting = int(request.max_citations or default_max_citations)
        if request.deep_search and max_citations_setting <= 9:
            max_citations_setting = 15
        # 深度检索模式：增加 top_k 以获取更多候选结果
        effective_top_k = (request.top_k or 8) * 2 if request.deep_search else (request.top_k or 8)
        agent_request = AgentRequest(
            query=request.message,
            filters=request.filters or {},
            top_k=effective_top_k,
            timeout_ms=settings.SUPERVISOR_TIMEOUT_MS,
            metadata={
                "session_id": session_id,
                "include_online_search": request.include_online_search,
                "original_query": request.message,
                "max_citations": max_citations_setting,
                "include_citations": request.include_citations,
                "deep_search": request.deep_search,
            }
        )

        # 调用 LangGraph MediArch Graph
        config = {"configurable": {"thread_id": session_id}}
        result = await mediarch_graph.ainvoke(
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
        # 深度检索模式：返回更多 citations（15-20个）
        max_citations = max_citations_setting
        wants_images = _wants_images_in_answer(request.message)
        # 优先使用 Synthesizer 透传的最终 citations（用于严格交叉验证对齐 [n]）
        final_citations_override = result.get("final_citations")
        if isinstance(final_citations_override, list) and final_citations_override:
            citations_full = final_citations_override
        else:
            citations_full = _extract_citations_from_items(
                items,
                max_citations=max_citations,
                allow_images=wants_images,
            )
        kg_data = _extract_knowledge_graph(worker_responses, result)
        recommended_questions = _extract_recommended_questions(result)
        # Prefer Synthesizer-provided image order (aligns with answer's [image:i] indices).
        image_references = result.get("image_references")
        if isinstance(image_references, list) and image_references:
            max_images = 8 if getattr(request, "deep_search", False) else 5
            image_refs = _extract_image_refs(image_references, api_base, max_images=max_images)
        else:
            max_images = 8 if getattr(request, "deep_search", False) else 5
            image_refs = _extract_image_refs(citations_full, api_base, max_images=max_images)

        answer_has_image_tokens = "[image:" in (final_answer or "")
        if image_refs and (not answer_has_image_tokens):
            image_refs = _order_image_refs_for_injection(image_refs)

        images = [ref.get("url") for ref in image_refs if ref.get("url")]

        # 让前端能渲染图片：优先尝试将图片插入正文附近；必要时再回退到末尾占位符。
        # 严格交叉验证模式下，为避免破坏格式，不做任何占位符注入。
        if not strict_cross_doc_mode and wants_images:
            final_answer = _inject_image_placeholders_inline(final_answer, image_refs)
            final_answer = _append_image_placeholders(final_answer, image_refs)

        # [FIX 2026-01-14] API 层最终对齐：避免“参考资料只有 N 条但正文出现 [N+1]”
        final_answer, citations_full = _postprocess_answer_and_align_citations(
            final_answer,
            citations_full,
            include_citations=bool(request.include_citations),
        )
        citations = citations_full if request.include_citations else []

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

            # 构建 AgentRequest
            default_max_citations = 15 if request.deep_search else 9
            max_citations_setting = int(request.max_citations or default_max_citations)
            if request.deep_search and max_citations_setting <= 9:
                max_citations_setting = 15
            # 深度检索模式：增加 top_k 以获取更多候选结果
            effective_top_k = (request.top_k or 8) * 2 if request.deep_search else (request.top_k or 8)
            agent_request = AgentRequest(
                query=request.message,
                filters=request.filters or {},
                top_k=effective_top_k,
                timeout_ms=settings.SUPERVISOR_TIMEOUT_MS,
                metadata={
                    "session_id": session_id,
                    "include_online_search": request.include_online_search,
                    "original_query": request.message,
                    "max_citations": max_citations_setting,
                    "include_citations": request.include_citations,
                    "deep_search": request.deep_search,
                }
            )

            # 调用 LangGraph MediArch Graph（使用 astream 获取中间状态）
            config = {"configurable": {"thread_id": session_id}}

            # 流式返回中间状态
            final_answer = ""
            all_items = []
            all_citations = []
            kg_data = None
            recommended_questions = []
            images = []
            image_references = []
            worker_responses = []
            final_citations_override = []
            neo4j_graph_sent = False
            strict_cross_doc_mode = False

            agent_completed = {
                "Orchestrator": False,
                "Neo4j": False,
                "Milvus": False,
                "MongoDB": False,
                "OnlineSearch": False,
                "Synthesizer": False,
            }
            last_node_labels: Dict[str, str] = {}

            async for event in mediarch_graph.astream(
                {"request": agent_request, "original_query": request.message},
                config=config,
                stream_mode="updates",
                subgraphs=True,
            ):
                for namespace, payload in _normalize_stream_event(event):
                    if not payload:
                        continue
                    event_items = list(payload.items())

                    # 节点级状态（用于前端思考过程）
                    for node_name, _ in event_items:
                        agent_key = _extract_agent_key(namespace, node_name)
                        if not agent_key:
                            continue
                        label = AGENT_NODE_LABELS.get(agent_key, {}).get(node_name)
                        if not label:
                            continue
                        last_node_labels[agent_key] = label
                        display_name = AGENT_DISPLAY_NAMES.get(agent_key, agent_key)
                        yield _create_agent_status_chunk(display_name, "running", label)

                    # 只处理顶层节点的聚合逻辑
                    if namespace:
                        continue

                    # LangGraph astream 返回的 event 格式: {node_name: node_output}
                    # 例如: {"orchestrator_agent": {...}}, {"neo4j_agent": {...}}
                    for node_name, node_output in event_items:
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
                            if not agent_completed["Orchestrator"]:
                                thought_text = last_node_labels.get("orchestrator") or thought
                                yield _create_agent_status_chunk("Orchestrator", "completed", thought_text)
                                agent_completed["Orchestrator"] = True

                        elif node_name == "neo4j_agent":
                            diagnostics = node_output.get("diagnostics", {}) if isinstance(node_output, dict) else {}
                            # Neo4j 完成
                            if isinstance(node_output, dict) and ("items" in node_output or "diagnostics" in node_output):
                                thought_text = last_node_labels.get("neo4j") or thought
                                yield _create_agent_status_chunk("Neo4j", "completed", thought_text)
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
                            # Milvus 完成
                            if isinstance(node_output, dict) and ("items" in node_output or "diagnostics" in node_output):
                                thought_text = last_node_labels.get("milvus") or thought
                                yield _create_agent_status_chunk("Milvus", "completed", thought_text)
                                agent_completed["Milvus"] = True

                        elif node_name == "mongodb_agent":
                            # MongoDB 完成
                            if isinstance(node_output, dict) and ("items" in node_output or "diagnostics" in node_output):
                                thought_text = last_node_labels.get("mongodb") or thought
                                yield _create_agent_status_chunk("MongoDB", "completed", thought_text)
                                agent_completed["MongoDB"] = True

                        elif node_name == "online_search_agent":
                            if isinstance(node_output, dict) and ("items" in node_output or "diagnostics" in node_output):
                                thought_text = last_node_labels.get("online_search") or thought
                                yield _create_agent_status_chunk("OnlineSearch", "completed", thought_text)
                                agent_completed["OnlineSearch"] = True

                        elif node_name == "knowledge_fusion":
                            # Knowledge Fusion 完成时，说明 Neo4j 和 Milvus 阶段1都已完成
                            if not agent_completed["Neo4j"]:
                                thought_text = last_node_labels.get("neo4j") or "实体匹配"
                                yield _create_agent_status_chunk("Neo4j", "completed", thought_text)
                                agent_completed["Neo4j"] = True
                            if not agent_completed["Milvus"]:
                                thought_text = last_node_labels.get("milvus") or "向量检索"
                                yield _create_agent_status_chunk("Milvus", "completed", thought_text)
                                agent_completed["Milvus"] = True

                        elif node_name == "prepare_parallel_workers":
                            # 准备阶段完成，Orchestrator 已完成
                            if not agent_completed["Orchestrator"]:
                                thought_text = last_node_labels.get("orchestrator") or thought
                                yield _create_agent_status_chunk("Orchestrator", "completed", thought_text)
                                agent_completed["Orchestrator"] = True

                    # 收集中间结果
                    for node_name, node_output in event_items:
                        if isinstance(node_output, dict):
                            if "items" in node_output:
                                items_data = node_output["items"]
                                if isinstance(items_data, list):
                                    all_items.extend(items_data)

                    # 从各节点输出中收集worker_responses和final_answer
                    for node_name, node_output in event_items:
                        if isinstance(node_output, dict):
                            # 收集 worker_responses
                            if "worker_responses" in node_output:
                                wr_list = node_output["worker_responses"]
                                if isinstance(wr_list, list):
                                    worker_responses.extend(wr_list)

                            # 检查 final_answer（通常来自 result_synthesizer_agent 或 push_answer_message）
                            if "final_answer" in node_output and node_output["final_answer"]:
                                if not agent_completed["Synthesizer"]:
                                    thought_text = last_node_labels.get("synthesizer")
                                    yield _create_agent_status_chunk("Synthesizer", "completed", thought_text)
                                    agent_completed["Synthesizer"] = True
                                final_answer = node_output["final_answer"]
                                # image_references（用于 images[] 顺序对齐）
                                if "image_references" in node_output and node_output["image_references"]:
                                    if isinstance(node_output["image_references"], list):
                                        image_references = node_output["image_references"]

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
                            # 严格交叉验证模式标记
                            if "strict_cross_doc" in node_output:
                                strict_cross_doc_mode = bool(node_output.get("strict_cross_doc"))

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
            # 深度检索模式：返回更多 citations（15-20个）
            max_citations = max_citations_setting
            wants_images = _wants_images_in_answer(request.message)
            all_citations_full = (
                final_citations_override
                if final_citations_override
                else _extract_citations_from_items(
                    all_items,
                    max_citations=max_citations,
                    allow_images=wants_images,
                )
            )

            # 提取知识图谱（如果还没有）
            if not kg_data:
                kg_data = _extract_knowledge_graph(worker_responses, {"answer_graph_data": None})

            # 提取图片（构建浏览器可访问的 /api/v1/documents/image?... URL）
            if isinstance(image_references, list) and image_references:
                max_images = 8 if getattr(request, "deep_search", False) else 5
                image_refs = _extract_image_refs(image_references, api_base, max_images=max_images)
            else:
                max_images = 8 if getattr(request, "deep_search", False) else 5
                image_refs = _extract_image_refs(all_citations_full, api_base, max_images=max_images)

            answer_has_image_tokens = "[image:" in (final_answer or "")
            if image_refs and (not answer_has_image_tokens):
                image_refs = _order_image_refs_for_injection(image_refs)

            images = [ref.get("url") for ref in image_refs if ref.get("url")]

            # 让前端能渲染图片：优先尝试插入正文附近；必要时回退到末尾占位符块。
            # 严格交叉验证模式下，为避免破坏固定输出格式，不注入占位符。
            if image_refs and not strict_cross_doc_mode and wants_images:
                final_answer = _inject_image_placeholders_inline(final_answer, image_refs)
                final_answer = _append_image_placeholders(final_answer, image_refs)

            # [FIX 2026-01-14] API 层最终对齐：避免流式返回时出现越界/乱引用
            final_answer, all_citations_full = _postprocess_answer_and_align_citations(
                final_answer,
                all_citations_full,
                include_citations=bool(request.include_citations),
            )
            all_citations = all_citations_full if request.include_citations else []

            # 逐字符流式发送答案（注意：这里已经注入了 [image:n]）
            if final_answer:
                for i in range(0, len(final_answer), 10):  # 每次发送10个字符
                    chunk_text = final_answer[i:i+10]
                    chunk = StreamingChatChunk(
                        chunk_type="content",
                        content=chunk_text,
                        is_final=False
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"
                    await asyncio.sleep(0.02)  # 模拟打字延迟

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

            # 历史记录也存储带占位符/插图的最终答案

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
