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
import hashlib
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

def _infer_node_type_from_neo4j(item: AgentItem) -> str:
    """
    从 Neo4j AgentItem 推断节点类型（使用 schema 定义的标签）

    Args:
        item: Neo4j Agent 返回的实体项

    Returns:
        节点类型（Hospital, DepartmentGroup, FunctionalZone, Space, DesignMethod等）
    """
    # 优先使用 entity_type（如果有）
    entity_type = item.entity_type or ""

    # 检查常见的 schema 标签
    if entity_type in ["Hospital", "DepartmentGroup", "FunctionalZone", "Space",
                       "DesignMethod", "DesignMethodCategory", "Case", "Source",
                       "MedicalService", "MedicalEquipment", "TreatmentMethod"]:
        return entity_type

    # 从名称推断类型
    return _infer_node_type_from_name(item.name or "", entity_type)


def _infer_node_type_from_name(name: str, label: str = "") -> str:
    """
    从节点名称和标签推断节点类型

    Args:
        name: 节点名称
        label: 节点标签（可选）

    Returns:
        节点类型
    """
    name_lower = name.lower()
    label_lower = label.lower()

    # 检查标签
    if label:
        if label in ["Hospital", "DepartmentGroup", "FunctionalZone", "Space",
                     "DesignMethod", "DesignMethodCategory", "Case", "Source"]:
            return label

    # 医院
    if "医院" in name or "hospital" in name_lower:
        return "Hospital"

    # 部门
    if any(dept in name for dept in ["门诊部", "急诊部", "医技部", "住院部"]):
        return "DepartmentGroup"

    # 功能分区
    if any(zone in name for zone in ["手术部", "急救区", "医技区", "护理单元", "放射影像中心",
                                      "检验科", "透析室", "消毒供应室", "重症监护室"]):
        return "FunctionalZone"

    # 空间
    if any(space in name for space in ["室", "间", "厅", "站", "区"]) and \
       not any(dept in name for dept in ["部", "科", "中心"]):
        return "Space"

    # 设计方法
    if any(method in name for method in ["设计", "方法", "模式", "策略", "布局", "划分"]):
        return "DesignMethod"

    # 案例
    if "案例" in name or "项目" in name or "case" in name_lower:
        return "Case"

    # 默认返回 Space（最常见的类型）
    return "Space"


def _infer_source_type(source_name: str) -> str:
    """
    从文档名称推断资料类型

    Args:
        source_name: 文档名称

    Returns:
        资料类型（规范标准、图集书籍、学术文献等）
    """
    lower = source_name.lower()

    if ("详图" in source_name) or ("图集" in source_name) or ("atlas" in lower):
        return "图集书籍"
    elif ("规范" in source_name) or ("标准" in source_name) or ("gb" in lower):
        return "规范标准"
    elif ("指南" in source_name) or ("手册" in source_name) or ("guide" in lower):
        return "政策文件"
    elif ("论文" in source_name) or ("期刊" in source_name) or ("journal" in lower):
        return "学术文献"
    else:
        return "项目文档"


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

    节点类型（遵循 medical_architecture.json schema）:
    - Hospital: 医院节点
    - DepartmentGroup: 部门节点（门诊部、急诊部等）
    - FunctionalZone: 功能分区节点（手术部、急救区等）
    - Space: 空间节点（手术室、诊室等）
    - DesignMethod: 设计方法节点
    - DesignMethodCategory: 设计方法分类节点
    - Case: 案例节点
    - Source: 资料来源节点

    边类型:
    - 来自 Neo4j 的实体关系（CONTAINS, GUIDES, MENTIONED_IN等）
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

        # 从 Neo4j 节点标签推断节点类型（使用 schema 定义的标签）
        node_type = _infer_node_type_from_neo4j(item)

        # 创建核心实体节点
        node = GraphNode(
            id=node_id,
            name=item.name or "未命名实体",
            type=node_type,
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
                # 从边的目标节点推断类型
                target_type = _infer_node_type_from_name(target_name, edge.get("target_label"))
                related_node = GraphNode(
                    id=target_id,
                    name=target_name,
                    type=target_type,
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

    # ========== 从 Milvus 结果构建文档源节点、知识点节点和引用 ==========
    doc_nodes: Dict[str, GraphNode] = {}
    knowledge_nodes: List[GraphNode] = []  # 存储知识点节点

    for item in milvus_items:
        # 提取文档来源
        attrs = item.attrs or {}
        source_doc = attrs.get("source_document") or item.name or "未知文档"
        stable_hash = hashlib.md5(str(source_doc).encode("utf-8")).hexdigest()[:10]
        doc_id = f"doc_{stable_hash}"

        # 创建文档源节点（使用 Source 类型，符合 schema）
        if doc_id not in doc_nodes:
            doc_node = GraphNode(
                id=doc_id,
                name=source_doc,
                type="Source",  # 使用 schema 定义的 Source 类型
                properties={
                    "source": source_doc,
                    "source_type": _infer_source_type(source_doc),
                    "title": source_doc,
                },
            )
            doc_nodes[doc_id] = doc_node

        # ========== 优化：使用提取的结构化知识点创建节点 ==========
        # 检查是否有提取的知识点
        extracted_kps = attrs.get("knowledge_points", [])

        if extracted_kps:
            # 使用提取的结构化知识点
            for kp in extracted_kps:
                kp_title = kp.get("title", "未命名知识点")
                kp_content = kp.get("content", "")
                kp_category = kp.get("category", "")
                applicable_spaces = kp.get("applicable_spaces", [])

                # 生成知识点ID
                knowledge_id = f"kp_{hashlib.md5((kp_title + source_doc).encode('utf-8')).hexdigest()[:10]}"

                # 创建知识点节点
                knowledge_node = GraphNode(
                    id=knowledge_id,
                    name=kp_title,
                    type="KnowledgePoint",
                    properties={
                        "title": kp_title,
                        "content": kp_content,
                        "category": kp_category,
                        "applicable_spaces": applicable_spaces,
                        "priority": kp.get("priority", "推荐"),
                        "source_ref": kp.get("source_ref", ""),
                        "source_document": source_doc,
                        "section": kp.get("section", ""),
                        "page_number": kp.get("page_number"),
                        "score": kp.get("similarity", item.score),
                    },
                    score=kp.get("similarity", item.score),
                )
                knowledge_nodes.append(knowledge_node)

                # 创建 知识点 → 资料来源 的边 (MENTIONED_IN)
                edge_key = f"{knowledge_id}-MENTIONED_IN-{doc_id}"
                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    graph_data.edges.append(GraphEdge(
                        source=knowledge_id,
                        target=doc_id,
                        relation="MENTIONED_IN",
                        weight=1.0,
                    ))
        else:
            # 回退：如果没有提取知识点，使用原始chunk内容
            snippet = ""
            for citation_dict in (item.citations or []):
                snippet = citation_dict.get("snippet", "")
                if snippet:
                    break

            item_name = item.name or "未命名知识点"
            knowledge_id = f"kp_{hashlib.md5((item_name + source_doc).encode('utf-8')).hexdigest()[:10]}"

            knowledge_node = GraphNode(
                id=knowledge_id,
                name=item_name[:50] + "..." if len(item_name) > 50 else item_name,
                type="KnowledgePoint",
                properties={
                    "content": item_name,
                    "snippet": snippet[:200] if snippet else "",
                    "score": item.score,
                    "source_document": source_doc,
                },
                score=item.score,
            )
            knowledge_nodes.append(knowledge_node)

            # 创建 知识点 → 资料来源 的边
            edge_key = f"{knowledge_id}-MENTIONED_IN-{doc_id}"
            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                graph_data.edges.append(GraphEdge(
                    source=knowledge_id,
                    target=doc_id,
                    relation="MENTIONED_IN",
                    weight=1.0,
                ))

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

    # 添加知识点节点
    for knowledge_node in knowledge_nodes:
        if knowledge_node.id not in seen_node_ids:
            seen_node_ids.add(knowledge_node.id)
            graph_data.nodes.append(knowledge_node)

    # ========== 创建实体到文档的引用边 ==========
    # 如果实体来源于某个文档，创建 MENTIONED_IN 边
    for item in neo4j_items:
        node_id = item.entity_id or f"neo4j_{item.name or id(item)}"

        for citation_dict in (item.citations or []):
            source_doc = citation_dict.get("source", "")
            if source_doc:
                stable_hash = hashlib.md5(str(source_doc).encode("utf-8")).hexdigest()[:10]
                doc_id = f"doc_{stable_hash}"
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

    # ========== 增强：补充层级结构骨架 ==========
    # 当 Neo4j 返回的节点缺少层级结构时，自动补充骨架节点
    # 目标：展示 医院 → 部门 → 功能分区 → 空间 的层级结构
    _enhance_graph_hierarchy(graph_data, query, seen_node_ids, seen_edge_keys)

    # ========== 新增：推断和关联设计方法 ==========
    # 从查询内容和知识点推断相关的设计方法，并建立关联
    _infer_and_link_design_methods(
        graph_data, query, neo4j_items, milvus_items,
        knowledge_nodes, seen_node_ids, seen_edge_keys
    )

    # ========== 新增：建立 Space → KnowledgePoint 关系 ==========
    # 根据知识点的 applicable_spaces 属性建立空间到知识点的 GUIDES 关系
    _link_spaces_to_knowledge_points(
        graph_data, knowledge_nodes, seen_edge_keys
    )

    # 统计信息
    graph_data.total_entities = len([n for n in graph_data.nodes if n.type not in ["Source", "KnowledgePoint"]])
    graph_data.total_relations = len(graph_data.edges)
    graph_data.total_citations = len(graph_data.citations)

    logger.info(
        f"[KnowledgeFusion→Graph] "
        f"nodes={len(graph_data.nodes)}, "
        f"edges={len(graph_data.edges)}, "
        f"citations={len(graph_data.citations)}"
    )

    return graph_data


def _enhance_graph_hierarchy(
    graph_data: AnswerGraphData,
    query: str,
    seen_node_ids: Set[str],
    seen_edge_keys: Set[str]
) -> None:
    """
    增强图谱层级结构

    当图谱中缺少层级结构时，根据查询内容自动补充骨架节点
    例如：查询"手术室"时，补充 综合医院 → 医技部 → 手术部 → 手术室 的路径
    """
    if not query:
        return

    # 检查是否已有 Hospital 节点
    has_hospital = any(n.type == "Hospital" for n in graph_data.nodes)
    has_space = any(n.type == "Space" for n in graph_data.nodes)

    # 如果已经有完整的层级结构，不需要补充
    if has_hospital and has_space:
        return

    # 根据查询内容推断需要补充的节点
    q = query.strip()

    def _add_node(node_id: str, name: str, node_type: str, properties: dict = None) -> None:
        if node_id in seen_node_ids:
            return
        seen_node_ids.add(node_id)
        graph_data.nodes.append(
            GraphNode(
                id=node_id,
                name=name,
                type=node_type,
                properties=properties or {"is_concept": True, "inferred": True},
            )
        )

    def _add_edge(src: str, tgt: str, rel: str, weight: float = 1.0) -> None:
        key = f"{src}-{rel}-{tgt}"
        if key in seen_edge_keys:
            return
        seen_edge_keys.add(key)
        graph_data.edges.append(GraphEdge(source=src, target=tgt, relation=rel, weight=weight))

    # 补充医院根节点
    hospital_id = "concept_hospital"
    if not has_hospital:
        _add_node(hospital_id, "综合医院", "Hospital")

    # 根据查询内容补充相关的部门和空间
    if "手术" in q:
        dept_id = "concept_dept_medical_tech"
        zone_id = "concept_zone_surgery"
        space_id = "concept_space_operating_room"

        _add_node(dept_id, "医技部", "DepartmentGroup")
        _add_node(zone_id, "手术部", "FunctionalZone")
        _add_node(space_id, "手术室", "Space")

        _add_edge(hospital_id, dept_id, "CONTAINS")
        _add_edge(dept_id, zone_id, "CONTAINS")
        _add_edge(zone_id, space_id, "CONTAINS")

    elif "急诊" in q:
        dept_id = "concept_dept_emergency"
        zone_id = "concept_zone_emergency_rescue"
        space_id = "concept_space_emergency_room"

        _add_node(dept_id, "急诊部", "DepartmentGroup")
        _add_node(zone_id, "急救区", "FunctionalZone")
        _add_node(space_id, "抢救室", "Space")

        _add_edge(hospital_id, dept_id, "CONTAINS")
        _add_edge(dept_id, zone_id, "CONTAINS")
        _add_edge(zone_id, space_id, "CONTAINS")

    elif "门诊" in q:
        dept_id = "concept_dept_outpatient"
        zone_id = "concept_zone_outpatient_clinic"
        space_id = "concept_space_clinic_room"

        _add_node(dept_id, "门诊部", "DepartmentGroup")
        _add_node(zone_id, "各科诊区", "FunctionalZone")
        _add_node(space_id, "诊室", "Space")

        _add_edge(hospital_id, dept_id, "CONTAINS")
        _add_edge(dept_id, zone_id, "CONTAINS")
        _add_edge(zone_id, space_id, "CONTAINS")

    elif "病房" in q or "住院" in q:
        dept_id = "concept_dept_inpatient"
        zone_id = "concept_zone_nursing_unit"
        space_id = "concept_space_ward"

        _add_node(dept_id, "住院部", "DepartmentGroup")
        _add_node(zone_id, "护理单元", "FunctionalZone")
        _add_node(space_id, "病房", "Space")

        _add_edge(hospital_id, dept_id, "CONTAINS")
        _add_edge(dept_id, zone_id, "CONTAINS")
        _add_edge(zone_id, space_id, "CONTAINS")


def _infer_and_link_design_methods(
    graph_data: AnswerGraphData,
    query: str,
    neo4j_items: List[AgentItem],
    milvus_items: List[AgentItem],
    knowledge_nodes: List[GraphNode],
    seen_node_ids: Set[str],
    seen_edge_keys: Set[str]
) -> None:
    """
    推断和关联设计方法

    混合策略:
    1. 优先使用 Neo4j 返回的设计方法节点（如果有）
    2. 从查询内容和 Milvus 结果推断相关的设计方法
    3. 建立完整的关联链路：空间 → 设计方法 → 知识点 → 资料来源
    """
    # 检查 Neo4j 是否已经返回了设计方法
    has_design_method = any(n.type == "DesignMethod" for n in graph_data.nodes)

    # 如果 Neo4j 已经有设计方法，直接关联知识点
    if has_design_method:
        _link_existing_design_methods_to_knowledge(
            graph_data, knowledge_nodes, seen_edge_keys
        )
        return

    # 否则，从查询和内容推断设计方法
    inferred_methods = _infer_design_methods_from_query_and_content(
        query, milvus_items
    )

    if not inferred_methods:
        return

    # 创建推断的设计方法节点
    for method_name, method_desc in inferred_methods:
        method_id = f"method_{hashlib.md5(method_name.encode('utf-8')).hexdigest()[:10]}"

        if method_id in seen_node_ids:
            continue

        seen_node_ids.add(method_id)
        method_node = GraphNode(
            id=method_id,
            name=method_name,
            type="DesignMethod",
            properties={
                "title": method_name,
                "description": method_desc,
                "inferred": True,  # 标记为推断的节点
            },
        )
        graph_data.nodes.append(method_node)

        # 关联到空间节点
        space_nodes = [n for n in graph_data.nodes if n.type == "Space"]
        for space_node in space_nodes:
            edge_key = f"{method_id}-GUIDES-{space_node.id}"
            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                graph_data.edges.append(GraphEdge(
                    source=method_id,
                    target=space_node.id,
                    relation="GUIDES",
                    weight=0.8,
                ))

        # 关联到知识点节点
        for knowledge_node in knowledge_nodes:
            # 检查知识点内容是否与设计方法相关
            content = knowledge_node.properties.get("content", "").lower()
            if any(keyword in content for keyword in method_name.lower().split()):
                edge_key = f"{method_id}-MENTIONED_IN-{knowledge_node.id}"
                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    graph_data.edges.append(GraphEdge(
                        source=method_id,
                        target=knowledge_node.id,
                        relation="MENTIONED_IN",
                        weight=0.9,
                    ))


def _link_existing_design_methods_to_knowledge(
    graph_data: AnswerGraphData,
    knowledge_nodes: List[GraphNode],
    seen_edge_keys: Set[str]
) -> None:
    """关联已有的设计方法节点到知识点"""
    design_methods = [n for n in graph_data.nodes if n.type == "DesignMethod"]

    for method in design_methods:
        method_name = method.name.lower()
        for knowledge_node in knowledge_nodes:
            content = knowledge_node.properties.get("content", "").lower()
            # 如果知识点内容包含设计方法名称，建立关联
            if method_name in content or any(
                keyword in content for keyword in method_name.split()
            ):
                edge_key = f"{method.id}-MENTIONED_IN-{knowledge_node.id}"
                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    graph_data.edges.append(GraphEdge(
                        source=method.id,
                        target=knowledge_node.id,
                        relation="MENTIONED_IN",
                        weight=0.9,
                    ))


def _infer_design_methods_from_query_and_content(
    query: str,
    milvus_items: List[AgentItem]
) -> List[tuple]:
    """
    从查询和 Milvus 内容推断设计方法

    Returns:
        List[tuple]: [(方法名称, 方法描述), ...]
    """
    methods = []
    q = query.lower()

    # 常见的设计方法关键词映射
    method_keywords = {
        "三区": ("三区划分", "将医疗空间划分为清洁区、准清洁区和污染区"),
        "双走廊": ("双走廊设计", "设置清洁走廊和污染走廊，实现洁污分流"),
        "洁污分流": ("洁污分流", "通过空间布局和流线设计实现清洁物品和污染物品的分离"),
        "中心": ("中心式布局", "将核心功能区域集中布置，周边为辅助区域"),
        "单元": ("单元式设计", "将功能空间划分为独立的功能单元"),
        "集中": ("集中式布局", "将相同或相关功能集中布置"),
        "分散": ("分散式布局", "将功能空间分散布置，提高灵活性"),
    }

    # 从查询中推断
    for keyword, (method_name, method_desc) in method_keywords.items():
        if keyword in q:
            methods.append((method_name, method_desc))

    # 从 Milvus 内容中推断
    for item in milvus_items:
        content = (item.name or "").lower()
        for keyword, (method_name, method_desc) in method_keywords.items():
            if keyword in content and (method_name, method_desc) not in methods:
                methods.append((method_name, method_desc))

    return methods[:3]  # 最多返回3个设计方法


def _link_spaces_to_knowledge_points(
    graph_data: AnswerGraphData,
    knowledge_nodes: List[GraphNode],
    seen_edge_keys: Set[str]
) -> None:
    """
    建立 Space/FunctionalZone → KnowledgePoint 的 GUIDES 关系

    根据知识点的 applicable_spaces 属性,将知识点关联到相应的空间节点。
    这样可以展示：空间 → 设计规范/知识点 → 资料来源 的完整链路。

    关系类型使用 GUIDES (反向),表示知识点指导空间设计。
    """
    # 获取所有空间和功能分区节点
    space_nodes = [n for n in graph_data.nodes if n.type in ["Space", "FunctionalZone"]]

    if not space_nodes or not knowledge_nodes:
        logger.info("[KnowledgeFusion] 无空间节点或知识点节点,跳过关系建立")
        return

    linked_count = 0

    for kp_node in knowledge_nodes:
        applicable_spaces = kp_node.properties.get("applicable_spaces", [])

        if not applicable_spaces:
            # 如果没有明确的 applicable_spaces,尝试从标题和内容中推断
            kp_title = kp_node.properties.get("title", "").lower()
            kp_content = kp_node.properties.get("content", "").lower()
            kp_text = kp_title + " " + kp_content

            # 尝试匹配空间节点名称
            for space_node in space_nodes:
                space_name = space_node.name.lower()

                # 简单的关键词匹配
                if space_name in kp_text or any(
                    keyword in kp_text
                    for keyword in space_name.split()
                    if len(keyword) > 1
                ):
                    # 创建 Space → KnowledgePoint 的 GUIDES 关系
                    # 注意：这里是反向的,实际上是知识点指导空间设计
                    edge_key = f"{kp_node.id}-GUIDES-{space_node.id}"
                    if edge_key not in seen_edge_keys:
                        seen_edge_keys.add(edge_key)
                        graph_data.edges.append(GraphEdge(
                            source=kp_node.id,
                            target=space_node.id,
                            relation="GUIDES",
                            weight=0.7,
                            properties={
                                "inferred": True,
                                "match_type": "keyword"
                            }
                        ))
                        linked_count += 1
        else:
            # 使用明确的 applicable_spaces 列表
            for applicable_space in applicable_spaces:
                applicable_space_lower = applicable_space.lower()

                # 查找匹配的空间节点
                for space_node in space_nodes:
                    space_name = space_node.name.lower()

                    # 精确匹配或包含匹配
                    if (
                        applicable_space_lower == space_name
                        or applicable_space_lower in space_name
                        or space_name in applicable_space_lower
                    ):
                        # 创建 KnowledgePoint → Space 的 GUIDES 关系
                        edge_key = f"{kp_node.id}-GUIDES-{space_node.id}"
                        if edge_key not in seen_edge_keys:
                            seen_edge_keys.add(edge_key)
                            graph_data.edges.append(GraphEdge(
                                source=kp_node.id,
                                target=space_node.id,
                                relation="GUIDES",
                                weight=0.9,
                                properties={
                                    "inferred": False,
                                    "match_type": "explicit"
                                }
                            ))
                            linked_count += 1

    logger.info(f"[KnowledgeFusion] 建立了 {linked_count} 条 KnowledgePoint → Space 的 GUIDES 关系")


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
