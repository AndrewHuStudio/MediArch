# backend/app/agents/knowledge_fusion/fusion.py
"""
Knowledge Fusion - Neo4j + Milvus 检索结果融合

核心功能:
1. 合并 Neo4j (图谱) 和 Milvus (向量) 的并行检索结果
2. 生成统一检索线索供 MongoDB 精确定位
3. 构建前端需要的答案图谱数据结构

架构流程:
```
Neo4j Agent ──┬──> Knowledge Fusion ──> unified_hints ──> MongoDB Agent
              │                     ──> graph_data   ──> Result Synthesizer
Milvus Agent ─┘
```

2025-11-25 创建
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from backend.app.agents.base_agent import AgentItem

logger = logging.getLogger("knowledge_fusion")


# ============================================================================
# 数据结构定义
# ============================================================================

@dataclass
class UnifiedHints:
    """统一检索线索 - 供 MongoDB/Online Search 使用"""

    # 实体信息
    entity_names: List[str] = field(default_factory=list)
    entity_types: List[str] = field(default_factory=list)

    # Chunk 信息
    chunk_ids: List[str] = field(default_factory=list)

    # 位置信息
    sections: List[str] = field(default_factory=list)
    page_ranges: List[tuple] = field(default_factory=list)

    # 关系信息
    relations: List[Dict[str, str]] = field(default_factory=list)

    # 搜索词
    search_terms: List[str] = field(default_factory=list)

    # 元信息
    neo4j_entity_count: int = 0
    milvus_chunk_count: int = 0
    fusion_score: float = 0.0


@dataclass
class GraphNode:
    """图谱节点 - 供前端可视化"""
    id: str
    name: str
    type: str  # "core_entity" | "related_entity" | "document_source" | "chunk"
    properties: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None


@dataclass
class GraphEdge:
    """图谱边 - 供前端可视化"""
    source: str
    target: str
    relation: str
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Citation:
    """引用信息 - 供前端 PDF 高亮"""
    chunk_id: str
    source: str  # 文档名
    page_number: Optional[int] = None
    page_range: Optional[List[int]] = None
    section: Optional[str] = None
    heading: Optional[str] = None
    sub_section: Optional[str] = None
    positions: Optional[List[Dict[str, Any]]] = None  # 归一化 bbox 坐标
    content_type: str = "text"  # "text" | "table" | "image"
    snippet: str = ""
    image_url: Optional[str] = None


@dataclass
class AnswerGraphData:
    """答案图谱数据 - 供前端展示"""
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)

    # 统计信息
    total_entities: int = 0
    total_relations: int = 0
    total_citations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（供 JSON 序列化）"""
        return {
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "type": n.type,
                    "properties": n.properties,
                    "score": n.score,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "relation": e.relation,
                    "weight": e.weight,
                    "properties": e.properties,
                }
                for e in self.edges
            ],
            "citations": [
                {
                    "chunk_id": c.chunk_id,
                    "source": c.source,
                    "page": c.page_number,
                    "page_range": c.page_range,
                    "section": c.section,
                    "heading": c.heading,
                    "sub_section": c.sub_section,
                    "positions": c.positions,
                    "content_type": c.content_type,
                    "snippet": c.snippet,
                    "image_url": c.image_url,
                }
                for c in self.citations
            ],
            "total_entities": self.total_entities,
            "total_relations": self.total_relations,
            "total_citations": self.total_citations,
        }


@dataclass
class KnowledgeFusionResult:
    """融合结果"""
    unified_hints: UnifiedHints
    graph_data: AnswerGraphData
    merged_items: List[AgentItem]
    diagnostics: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 融合核心函数
# ============================================================================

def fuse_retrieval_results(
    neo4j_items: List[AgentItem],
    milvus_items: List[AgentItem],
    query: str = "",
    max_entities: int = 20,
    max_chunks: int = 30,
) -> KnowledgeFusionResult:
    """
    融合 Neo4j 和 Milvus 的检索结果

    Args:
        neo4j_items: Neo4j Agent 返回的实体结果
        milvus_items: Milvus Agent 返回的向量检索结果
        query: 原始查询
        max_entities: 最大实体数量
        max_chunks: 最大 chunk 数量

    Returns:
        KnowledgeFusionResult: 包含统一线索、图谱数据、合并结果
    """
    logger.info(
        f"[KnowledgeFusion] 开始融合: Neo4j={len(neo4j_items)} items, "
        f"Milvus={len(milvus_items)} items"
    )

    # 1. 构建统一检索线索
    unified_hints = build_unified_hints(
        neo4j_items, milvus_items, query, max_entities, max_chunks
    )

    # 2. 构建答案图谱数据
    graph_data = build_answer_graph_data(neo4j_items, milvus_items, query)

    # 3. 合并去重 items
    merged_items = _merge_and_deduplicate_items(neo4j_items, milvus_items)

    # 4. 计算融合质量分数
    fusion_score = _calculate_fusion_score(
        neo4j_items, milvus_items, unified_hints
    )
    unified_hints.fusion_score = fusion_score

    # 5. 诊断信息
    diagnostics = {
        "neo4j_entity_count": len(neo4j_items),
        "milvus_chunk_count": len(milvus_items),
        "merged_item_count": len(merged_items),
        "unique_entities": len(unified_hints.entity_names),
        "unique_chunks": len(unified_hints.chunk_ids),
        "fusion_score": fusion_score,
        "graph_nodes": len(graph_data.nodes),
        "graph_edges": len(graph_data.edges),
    }

    logger.info(
        f"[KnowledgeFusion] 融合完成: "
        f"entities={diagnostics['unique_entities']}, "
        f"chunks={diagnostics['unique_chunks']}, "
        f"score={fusion_score:.2f}"
    )

    return KnowledgeFusionResult(
        unified_hints=unified_hints,
        graph_data=graph_data,
        merged_items=merged_items,
        diagnostics=diagnostics,
    )


def build_unified_hints(
    neo4j_items: List[AgentItem],
    milvus_items: List[AgentItem],
    query: str = "",
    max_entities: int = 20,
    max_chunks: int = 30,
) -> UnifiedHints:
    """
    构建统一检索线索

    Neo4j 贡献:
    - 实体名称和类型
    - 实体间关系
    - 知识覆盖领域

    Milvus 贡献:
    - chunk_ids
    - 章节信息
    - 页码范围
    """
    hints = UnifiedHints()

    entity_names_set: Set[str] = set()
    entity_types_set: Set[str] = set()
    chunk_ids_set: Set[str] = set()
    sections_set: Set[str] = set()
    search_terms_set: Set[str] = set()

    # 添加原始查询作为搜索词
    if query:
        search_terms_set.add(query)

    # ========== 处理 Neo4j 结果 ==========
    for item in neo4j_items:
        # 实体名称
        if item.name:
            entity_names_set.add(item.name)
            search_terms_set.add(item.name)

        # 实体类型/标签
        if item.label:
            entity_types_set.add(item.label)

        # 从 attrs 提取更多信息
        attrs = item.attrs or {}

        # 别名
        aliases = attrs.get("aliases", [])
        if isinstance(aliases, list):
            for alias in aliases:
                if alias:
                    entity_names_set.add(str(alias))
                    search_terms_set.add(str(alias))

        # 关系信息
        for edge in (item.edges or []):
            relation = {
                "source": item.name or "",
                "relation": edge.get("type", "RELATED_TO"),
                "target": edge.get("target", ""),
            }
            if relation["source"] and relation["target"]:
                hints.relations.append(relation)
                # 目标实体也作为搜索词
                target_name = edge.get("target", "")
                if target_name:
                    entity_names_set.add(target_name)
                    search_terms_set.add(target_name)

    # ========== 处理 Milvus 结果 ==========
    for item in milvus_items:
        # 从 citations 提取 chunk 信息
        for citation in (item.citations or []):
            # chunk_id
            chunk_id = citation.get("chunk_id")
            if chunk_id:
                chunk_ids_set.add(chunk_id)

            # 章节
            section = citation.get("section")
            if section:
                sections_set.add(section)

            # 页码范围
            page_number = citation.get("page_number")
            if page_number:
                hints.page_ranges.append((page_number, page_number))

        # 从 attrs 提取信息
        attrs = item.attrs or {}

        # attribute_text 可能包含有用的实体
        attr_text = attrs.get("attribute_text", "")
        if attr_text:
            # 简单提取中文词（2-6字）
            chinese_words = re.findall(r'[\u4e00-\u9fa5]{2,6}', attr_text)
            for word in chinese_words[:5]:  # 限制数量
                search_terms_set.add(word)

        # 实体名称
        if item.name:
            entity_names_set.add(item.name)
            search_terms_set.add(item.name)

    # ========== 合并去重 ==========
    hints.entity_names = list(entity_names_set)[:max_entities]
    hints.entity_types = list(entity_types_set)
    hints.chunk_ids = list(chunk_ids_set)[:max_chunks]
    hints.sections = list(sections_set)
    hints.search_terms = list(search_terms_set)[:30]  # 限制搜索词数量

    # 统计
    hints.neo4j_entity_count = len(neo4j_items)
    hints.milvus_chunk_count = len(milvus_items)

    logger.info(
        f"[KnowledgeFusion→Hints] "
        f"entities={len(hints.entity_names)}, "
        f"chunks={len(hints.chunk_ids)}, "
        f"relations={len(hints.relations)}, "
        f"search_terms={len(hints.search_terms)}"
    )

    return hints


def build_answer_graph_data(
    neo4j_items: List[AgentItem],
    milvus_items: List[AgentItem],
    query: str = "",
) -> AnswerGraphData:
    """
    构建答案图谱数据（供前端可视化）

    节点类型:
    - core_entity: 核心实体（来自 Neo4j）
    - related_entity: 关联实体（来自 Neo4j 边）
    - document_source: 文档来源（来自 Milvus/MongoDB）

    边类型:
    - 来自 Neo4j 的实体关系
    - 实体到文档的引用关系
    """
    graph_data = AnswerGraphData()

    seen_node_ids: Set[str] = set()
    seen_edge_keys: Set[str] = set()

    # ========== 从 Neo4j 结果构建核心实体节点 ==========
    for item in neo4j_items:
        node_id = item.entity_id or f"neo4j_{item.name or id(item)}"

        if node_id in seen_node_ids:
            continue
        seen_node_ids.add(node_id)

        # 创建核心实体节点
        node = GraphNode(
            id=node_id,
            name=item.name or "未命名实体",
            type="core_entity",
            properties=item.attrs or {},
            score=item.score,
        )
        graph_data.nodes.append(node)

        # 处理关联边
        for edge in (item.edges or []):
            target_name = edge.get("target", "")
            target_id = edge.get("target_id") or f"neo4j_{target_name}"
            relation_type = edge.get("type", "RELATED_TO")

            # 创建关联实体节点（如果不存在）
            if target_id not in seen_node_ids and target_name:
                seen_node_ids.add(target_id)
                related_node = GraphNode(
                    id=target_id,
                    name=target_name,
                    type="related_entity",
                    properties=edge.get("properties", {}),
                )
                graph_data.nodes.append(related_node)

            # 创建边
            edge_key = f"{node_id}-{relation_type}-{target_id}"
            if edge_key not in seen_edge_keys and target_name:
                seen_edge_keys.add(edge_key)
                graph_edge = GraphEdge(
                    source=node_id,
                    target=target_id,
                    relation=relation_type,
                    weight=edge.get("score", 1.0),
                    properties=edge.get("properties", {}),
                )
                graph_data.edges.append(graph_edge)

    # ========== 从 Milvus 结果构建文档源节点和引用 ==========
    doc_nodes: Dict[str, GraphNode] = {}

    for item in milvus_items:
        # 提取文档来源
        attrs = item.attrs or {}
        source_doc = attrs.get("source_document") or item.name or "未知文档"
        doc_id = f"doc_{hash(source_doc) % 10000}"

        # 创建文档源节点
        if doc_id not in doc_nodes:
            doc_node = GraphNode(
                id=doc_id,
                name=source_doc,
                type="document_source",
                properties={"source": source_doc},
            )
            doc_nodes[doc_id] = doc_node

        # 处理引用信息
        for citation_dict in (item.citations or []):
            citation = Citation(
                chunk_id=citation_dict.get("chunk_id", ""),
                source=citation_dict.get("source", source_doc),
                page_number=citation_dict.get("page_number"),
                page_range=citation_dict.get("page_range"),
                section=citation_dict.get("section"),
                heading=citation_dict.get("heading"),
                sub_section=citation_dict.get("sub_section"),
                positions=citation_dict.get("positions"),
                content_type=citation_dict.get("content_type", "text"),
                snippet=citation_dict.get("snippet", ""),
                image_url=citation_dict.get("image_url"),
            )
            graph_data.citations.append(citation)

    # 添加文档节点
    for doc_id, doc_node in doc_nodes.items():
        if doc_id not in seen_node_ids:
            seen_node_ids.add(doc_id)
            graph_data.nodes.append(doc_node)

    # ========== 创建实体到文档的引用边 ==========
    # 如果实体来源于某个文档，创建 MENTIONED_IN 边
    for item in neo4j_items:
        node_id = item.entity_id or f"neo4j_{item.name or id(item)}"

        for citation_dict in (item.citations or []):
            source_doc = citation_dict.get("source", "")
            if source_doc:
                doc_id = f"doc_{hash(source_doc) % 10000}"
                edge_key = f"{node_id}-MENTIONED_IN-{doc_id}"

                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    ref_edge = GraphEdge(
                        source=node_id,
                        target=doc_id,
                        relation="MENTIONED_IN",
                        weight=0.8,
                    )
                    graph_data.edges.append(ref_edge)

    # 统计
    graph_data.total_entities = sum(
        1 for n in graph_data.nodes
        if n.type in ("core_entity", "related_entity")
    )
    graph_data.total_relations = len(graph_data.edges)
    graph_data.total_citations = len(graph_data.citations)

    logger.info(
        f"[KnowledgeFusion→Graph] "
        f"nodes={len(graph_data.nodes)}, "
        f"edges={len(graph_data.edges)}, "
        f"citations={len(graph_data.citations)}"
    )

    return graph_data


# ============================================================================
# 辅助函数
# ============================================================================

def _merge_and_deduplicate_items(
    neo4j_items: List[AgentItem],
    milvus_items: List[AgentItem],
) -> List[AgentItem]:
    """合并并去重 items"""
    all_items = list(neo4j_items) + list(milvus_items)

    # 基于 entity_id 去重
    seen_ids: Set[str] = set()
    deduplicated: List[AgentItem] = []

    for item in all_items:
        key = item.entity_id or str(id(item))
        if key not in seen_ids:
            seen_ids.add(key)
            deduplicated.append(item)

    # 按分数排序
    deduplicated.sort(key=lambda x: x.score or 0.0, reverse=True)

    return deduplicated


def _calculate_fusion_score(
    neo4j_items: List[AgentItem],
    milvus_items: List[AgentItem],
    hints: UnifiedHints,
) -> float:
    """
    计算融合质量分数 (0-1)

    评估维度:
    - 实体覆盖度: Neo4j 提供的实体数量
    - Chunk 覆盖度: Milvus 提供的 chunk 数量
    - 关系丰富度: 实体间关系数量
    - 互补性: 两边结果的互补程度
    """
    # 基础分数
    entity_score = min(len(hints.entity_names) / 10.0, 1.0)  # 10个实体得满分
    chunk_score = min(len(hints.chunk_ids) / 15.0, 1.0)  # 15个chunk得满分
    relation_score = min(len(hints.relations) / 10.0, 1.0)  # 10条关系得满分

    # 互补性分数：两边都有结果时更高
    has_neo4j = len(neo4j_items) > 0
    has_milvus = len(milvus_items) > 0
    complementary_score = 1.0 if (has_neo4j and has_milvus) else 0.5

    # 加权平均
    fusion_score = (
        entity_score * 0.3 +
        chunk_score * 0.3 +
        relation_score * 0.2 +
        complementary_score * 0.2
    )

    return round(fusion_score, 2)


def extract_entities_from_chunks(milvus_items: List[AgentItem]) -> List[str]:
    """
    从 Milvus chunks 中反推实体名称

    用于 Neo4j 检索失败时的补充
    """
    entities: Set[str] = set()

    for item in milvus_items:
        # 从 snippet 提取中文实体词
        snippet = item.snippet or ""
        chinese_words = re.findall(r'[\u4e00-\u9fa5]{2,6}', snippet)

        # 过滤常见停用词
        stopwords = {"的", "是", "在", "和", "了", "有", "为", "与"}
        for word in chinese_words[:10]:
            if word not in stopwords:
                entities.add(word)

        # 从 attrs 提取
        attrs = item.attrs or {}
        attr_text = attrs.get("attribute_text", "")
        if attr_text:
            attr_words = re.findall(r'[\u4e00-\u9fa5]{2,6}', attr_text)
            for word in attr_words[:5]:
                if word not in stopwords:
                    entities.add(word)

    return list(entities)[:20]
