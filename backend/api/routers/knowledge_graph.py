# backend/api/routers/knowledge_graph.py
"""
知识图谱 API 路由

核心功能:
- POST /api/v1/kg/subgraph - 获取搜索适应性子图（多层级深度）
- GET /api/v1/kg/node/{node_id} - 获取节点详情
- GET /api/v1/kg/statistics - 获取图谱统计信息
"""

import logging
from typing import List, Dict, Any, Optional
from enum import Enum

from fastapi import APIRouter, HTTPException, Query, status, Depends
from pydantic import BaseModel, Field
from neo4j import GraphDatabase

logger = logging.getLogger("mediarch_api")

router = APIRouter()


# ============================================================================
# 请求/响应模型
# ============================================================================

class QueryFocusType(str, Enum):
    """查询焦点类型"""
    HOSPITAL = "hospital"          # 医院级（返回完整骨架）
    DEPARTMENT = "department"       # 部门级（返回部门子树）
    ZONE = "zone"                  # 功能分区级
    SPACE = "space"                # 空间级（返回空间+父路径+关联知识）
    DESIGN_METHOD = "design_method" # 设计方法级
    CASE = "case"                  # 案例级


class SubgraphRequest(BaseModel):
    """子图提取请求"""
    query: str = Field(..., description="用户查询文本")
    focus_type: Optional[QueryFocusType] = Field(None, description="焦点类型（自动推断或手动指定）")
    focus_node_name: Optional[str] = Field(None, description="焦点节点名称（如'手术室'、'急诊部'）")
    depth: int = Field(default=3, ge=1, le=5, description="提取深度（1-5）")
    include_design_methods: bool = Field(default=True, description="是否包含设计方法")
    include_cases: bool = Field(default=True, description="是否包含案例")
    include_sources: bool = Field(default=True, description="是否包含来源节点")
    max_nodes: int = Field(default=200, ge=10, le=500, description="最大节点数")
    perspective_filter: Optional[List[str]] = Field(None, description="视角过滤（规范要求、设计指导、实践案例、研究洞察）")


class NodeData(BaseModel):
    """节点数据"""
    id: str = Field(..., description="节点ID")
    label: str = Field(..., description="节点标签（Hospital, Space, DesignMethod等）")
    name: str = Field(..., description="节点名称")
    is_concept: bool = Field(default=False, description="是否为概念节点")
    properties: Dict[str, Any] = Field(default_factory=dict, description="节点属性")
    layer: int = Field(..., description="节点层级（0=hospital, 1=department, 2=zone, 3=space）")


class EdgeData(BaseModel):
    """边数据"""
    source: str = Field(..., description="源节点ID")
    target: str = Field(..., description="目标节点ID")
    type: str = Field(..., description="关系类型（CONTAINS, GUIDES, MENTIONED_IN等）")
    properties: Dict[str, Any] = Field(default_factory=dict, description="关系属性")


class LayoutHint(BaseModel):
    """布局提示"""
    node_id: str
    x: Optional[float] = None
    y: Optional[float] = None
    fixed: bool = False
    group: Optional[str] = None  # 用于分组渲染


class SubgraphResponse(BaseModel):
    """子图响应"""
    nodes: List[NodeData] = Field(..., description="节点列表")
    edges: List[EdgeData] = Field(..., description="边列表")
    layout_hints: List[LayoutHint] = Field(default_factory=list, description="前端布局提示")
    focus_node_id: Optional[str] = Field(None, description="焦点节点ID")
    statistics: Dict[str, int] = Field(default_factory=dict, description="子图统计信息")
    query: str = Field(..., description="原始查询")


class NodeDetailResponse(BaseModel):
    """节点详情响应"""
    node: NodeData
    related_chunks: List[str] = Field(default_factory=list, description="关联的chunk IDs")
    related_sources: List[Dict[str, Any]] = Field(default_factory=list, description="关联的来源节点")
    neighbors: Dict[str, List[NodeData]] = Field(default_factory=dict, description="邻居节点（按关系分组）")


class GraphStatistics(BaseModel):
    """图谱统计"""
    total_nodes: int
    total_relationships: int
    node_type_counts: Dict[str, int]
    relationship_type_counts: Dict[str, int]
    concept_node_count: int
    case_count: int
    design_method_count: int


# ============================================================================
# 依赖注入
# ============================================================================

def get_neo4j_driver():
    """获取 Neo4j driver"""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(
            os.getenv("NEO4J_USER", "neo4j"),
            os.getenv("NEO4J_PASSWORD", "password")
        )
    )
    try:
        yield driver
    finally:
        driver.close()


# ============================================================================
# 辅助函数
# ============================================================================

def infer_focus_type(query: str) -> QueryFocusType:
    """从查询文本推断焦点类型"""
    query_lower = query.lower()

    # 设计方法关键词
    if any(kw in query_lower for kw in ["方法", "模式", "策略", "原则", "设计"]):
        return QueryFocusType.DESIGN_METHOD

    # 案例关键词
    if any(kw in query_lower for kw in ["案例", "项目", "实例", "医院"]) and \
       any(kw in query_lower for kw in ["某", "北京", "上海", "改造", "新建"]):
        return QueryFocusType.CASE

    # 空间关键词
    if any(kw in query_lower for kw in ["室", "房", "间", "区", "厅", "站"]):
        return QueryFocusType.SPACE

    # 部门关键词
    if any(kw in query_lower for kw in ["部", "科", "中心"]):
        return QueryFocusType.DEPARTMENT

    # 默认返回医院级
    return QueryFocusType.HOSPITAL


def extract_focus_node_name(query: str, focus_type: QueryFocusType) -> Optional[str]:
    """从查询文本提取焦点节点名称"""
    # 简单的关键词匹配，生产环境应使用 NER
    keywords = {
        QueryFocusType.SPACE: ["手术室", "手术间", "抢救室", "诊室", "病房", "护士站"],
        QueryFocusType.DEPARTMENT: ["急诊部", "门诊部", "医技部", "住院部"],
        QueryFocusType.ZONE: ["手术部", "急救区", "医技区", "护理单元"],
    }

    if focus_type in keywords:
        for keyword in keywords[focus_type]:
            if keyword in query:
                return keyword

    return None


def calculate_layer(label: str) -> int:
    """根据节点标签计算层级"""
    layer_map = {
        "Hospital": 0,
        "DepartmentGroup": 1,
        "FunctionalZone": 2,
        "Space": 3,
        "DesignMethod": 4,
        "DesignMethodCategory": 4,
        "Case": 5,
        "Source": 6,
    }
    return layer_map.get(label, 7)


def generate_layout_hints(nodes: List[NodeData], edges: List[EdgeData],
                          focus_node_id: Optional[str]) -> List[LayoutHint]:
    """生成前端布局提示（简单的层级布局）"""
    hints = []

    # 按层级分组
    layer_groups = {}
    for node in nodes:
        layer = node.layer
        if layer not in layer_groups:
            layer_groups[layer] = []
        layer_groups[layer].append(node)

    # 为每层分配 y 坐标，同层节点横向排列
    y_spacing = 150
    x_spacing = 200

    for layer, layer_nodes in sorted(layer_groups.items()):
        y = layer * y_spacing
        total_width = len(layer_nodes) * x_spacing
        start_x = -total_width / 2

        for i, node in enumerate(layer_nodes):
            x = start_x + i * x_spacing
            hints.append(LayoutHint(
                node_id=node.id,
                x=x,
                y=y,
                fixed=(node.id == focus_node_id),
                group=f"layer_{layer}"
            ))

    return hints


# ============================================================================
# API 端点
# ============================================================================

@router.post("/kg/subgraph", response_model=SubgraphResponse, summary="获取搜索适应性子图")
async def get_adaptive_subgraph(
    request: SubgraphRequest,
    driver = Depends(get_neo4j_driver)
):
    """
    根据查询自动提取适应性子图，支持多层级深度展示

    核心特性:
    1. 自动推断查询焦点类型（医院/部门/空间/设计方法/案例）
    2. 根据焦点类型返回不同的子图结构
    3. 多层级深度（默认depth=3）
    4. 包含设计方法、案例、来源节点
    5. 提供前端布局提示

    示例:
    - 查询"手术室" → 返回手术室节点 + 父路径 + 关联设计方法 + 案例 + 来源
    - 查询"急诊部" → 返回急诊部及其下所有功能分区和空间
    - 查询"三区划分法" → 返回该方法 + 适用空间 + 案例 + 来源
    """
    try:
        logger.info(f"[KG Subgraph] 查询: {request.query}")

        # 1. 推断焦点类型和节点名称
        focus_type = request.focus_type or infer_focus_type(request.query)
        focus_node_name = request.focus_node_name or extract_focus_node_name(request.query, focus_type)

        logger.info(f"[KG Subgraph] 焦点类型: {focus_type}, 节点: {focus_node_name}")

        # 2. 根据焦点类型构建 Cypher 查询
        nodes = []
        edges = []
        focus_node_id = None

        # 获取数据库名称
        import os
        database = os.getenv("NEO4J_DATABASE", "neo4j")

        with driver.session(database=database) as session:

            if focus_type == QueryFocusType.HOSPITAL:
                # 返回完整医院骨架（Hospital → Department → Zone → Space）
                result = session.run("""
                    MATCH path = (h:Hospital {is_concept: true})-[:CONTAINS*1..3]->(descendant)
                    RETURN h, descendant, relationships(path) as rels
                    LIMIT $max_nodes
                """, max_nodes=request.max_nodes)

                for record in result:
                    h_node = record["h"]
                    desc_node = record["descendant"]

                    # 添加节点
                    for node in [h_node, desc_node]:
                        node_id = str(node.id)
                        if not any(n.id == node_id for n in nodes):
                            nodes.append(NodeData(
                                id=node_id,
                                label=list(node.labels)[0],
                                name=node.get("name", node.get("title", "Unknown")),
                                is_concept=node.get("is_concept", False),
                                properties=dict(node),
                                layer=calculate_layer(list(node.labels)[0])
                            ))

                    # 添加边
                    for rel in record["rels"]:
                        edges.append(EdgeData(
                            source=str(rel.start_node.id),
                            target=str(rel.end_node.id),
                            type=rel.type,
                            properties=dict(rel)
                        ))

            elif focus_type == QueryFocusType.SPACE and focus_node_name:
                # 空间级查询：空间节点 + 父路径 + 关联设计方法 + 案例 + 来源
                result = session.run("""
                    // 1. 找到焦点空间节点
                    MATCH (space:Space {name: $space_name, is_concept: true})

                    // 2. 获取父路径（空间 <- 功能分区 <- 部门 <- 医院）
                    OPTIONAL MATCH parent_path = (space)<-[:CONTAINS*1..3]-(ancestor)

                    // 3. 获取指导该空间的设计方法
                    OPTIONAL MATCH (method:DesignMethod)-[guides:GUIDES]->(space)
                    OPTIONAL MATCH (method)-[:IS_TYPE_OF]->(category:DesignMethodCategory)

                    // 4. 获取相关案例
                    OPTIONAL MATCH (case:Case)-[:REFERS_TO]->(space)
                    WHERE case.quality_score >= 0.7

                    // 5. 获取来源节点
                    OPTIONAL MATCH (space)-[mentioned:MENTIONED_IN]->(source:Source)

                    // 返回所有相关节点和关系
                    RETURN space,
                           collect(DISTINCT ancestor) as ancestors,
                           collect(DISTINCT method) as methods,
                           collect(DISTINCT category) as categories,
                           collect(DISTINCT case) as cases,
                           collect(DISTINCT source) as sources,
                           collect(DISTINCT guides) as guides_rels,
                           collect(DISTINCT mentioned) as mentioned_rels,
                           relationships(parent_path) as parent_rels
                    LIMIT 1
                """, space_name=focus_node_name)

                record = result.single()
                if not record:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"未找到空间节点: {focus_node_name}"
                    )

                # 添加焦点空间节点
                space_node = record["space"]
                focus_node_id = str(space_node.id)
                nodes.append(NodeData(
                    id=focus_node_id,
                    label="Space",
                    name=space_node.get("name"),
                    is_concept=space_node.get("is_concept", False),
                    properties=dict(space_node),
                    layer=3
                ))

                # 添加祖先节点
                for ancestor in record["ancestors"]:
                    if ancestor:
                        node_id = str(ancestor.id)
                        nodes.append(NodeData(
                            id=node_id,
                            label=list(ancestor.labels)[0],
                            name=ancestor.get("name", ancestor.get("title", "Unknown")),
                            is_concept=ancestor.get("is_concept", False),
                            properties=dict(ancestor),
                            layer=calculate_layer(list(ancestor.labels)[0])
                        ))

                # 添加父路径关系
                if record["parent_rels"]:
                    for rel in record["parent_rels"]:
                        edges.append(EdgeData(
                            source=str(rel.start_node.id),
                            target=str(rel.end_node.id),
                            type=rel.type,
                            properties=dict(rel)
                        ))

                # 添加设计方法节点
                if request.include_design_methods:
                    for method in record["methods"]:
                        if method:
                            node_id = str(method.id)
                            nodes.append(NodeData(
                                id=node_id,
                                label="DesignMethod",
                                name=method.get("title"),
                                is_concept=method.get("is_concept", False),
                                properties=dict(method),
                                layer=4
                            ))

                    for category in record["categories"]:
                        if category:
                            node_id = str(category.id)
                            if not any(n.id == node_id for n in nodes):
                                nodes.append(NodeData(
                                    id=node_id,
                                    label="DesignMethodCategory",
                                    name=category.get("name"),
                                    is_concept=category.get("is_concept", False),
                                    properties=dict(category),
                                    layer=4
                                ))

                    for rel in record["guides_rels"]:
                        if rel:
                            edges.append(EdgeData(
                                source=str(rel.start_node.id),
                                target=str(rel.end_node.id),
                                type=rel.type,
                                properties=dict(rel)
                            ))

                # 添加案例节点
                if request.include_cases:
                    for case in record["cases"]:
                        if case:
                            node_id = str(case.id)
                            nodes.append(NodeData(
                                id=node_id,
                                label="Case",
                                name=case.get("title"),
                                is_concept=False,
                                properties=dict(case),
                                layer=5
                            ))

                # 添加来源节点
                if request.include_sources:
                    for source in record["sources"]:
                        if source:
                            node_id = str(source.id)
                            nodes.append(NodeData(
                                id=node_id,
                                label="Source",
                                name=source.get("title"),
                                is_concept=False,
                                properties=dict(source),
                                layer=6
                            ))

                    for rel in record["mentioned_rels"]:
                        if rel:
                            # 应用视角过滤
                            if request.perspective_filter:
                                perspective = rel.get("perspective", "")
                                if perspective not in request.perspective_filter:
                                    continue

                            edges.append(EdgeData(
                                source=str(rel.start_node.id),
                                target=str(rel.end_node.id),
                                type=rel.type,
                                properties=dict(rel)
                            ))

            elif focus_type == QueryFocusType.DESIGN_METHOD and focus_node_name:
                # 设计方法查询：方法 + 分类 + 适用空间 + 案例 + 来源
                result = session.run("""
                    MATCH (method:DesignMethod)
                    WHERE method.title CONTAINS $method_name OR method.name CONTAINS $method_name

                    // 获取方法分类
                    OPTIONAL MATCH (method)-[:IS_TYPE_OF]->(category:DesignMethodCategory)

                    // 获取指导的空间
                    OPTIONAL MATCH (method)-[guides:GUIDES]->(space)
                    WHERE space.is_concept = true

                    // 获取相关案例
                    OPTIONAL MATCH (case:Case)-[:REFERS_TO]->(method)

                    // 获取来源
                    OPTIONAL MATCH (method)-[mentioned:MENTIONED_IN]->(source:Source)

                    RETURN method, category,
                           collect(DISTINCT space) as spaces,
                           collect(DISTINCT case) as cases,
                           collect(DISTINCT source) as sources,
                           collect(DISTINCT guides) as guides_rels,
                           collect(DISTINCT mentioned) as mentioned_rels
                    LIMIT 5
                """, method_name=focus_node_name)

                for record in result:
                    method_node = record["method"]
                    if not focus_node_id:
                        focus_node_id = str(method_node.id)

                    # 添加方法节点
                    nodes.append(NodeData(
                        id=str(method_node.id),
                        label="DesignMethod",
                        name=method_node.get("title"),
                        is_concept=method_node.get("is_concept", False),
                        properties=dict(method_node),
                        layer=4
                    ))

                    # 添加分类节点
                    if record["category"]:
                        category = record["category"]
                        nodes.append(NodeData(
                            id=str(category.id),
                            label="DesignMethodCategory",
                            name=category.get("name"),
                            is_concept=category.get("is_concept", False),
                            properties=dict(category),
                            layer=4
                        ))

                    # 添加适用空间
                    for space in record["spaces"]:
                        if space:
                            nodes.append(NodeData(
                                id=str(space.id),
                                label=list(space.labels)[0],
                                name=space.get("name", space.get("title")),
                                is_concept=space.get("is_concept", False),
                                properties=dict(space),
                                layer=calculate_layer(list(space.labels)[0])
                            ))

                    # 添加关系...
                    for rel in record["guides_rels"]:
                        if rel:
                            edges.append(EdgeData(
                                source=str(rel.start_node.id),
                                target=str(rel.end_node.id),
                                type=rel.type,
                                properties=dict(rel)
                            ))

        # 3. 生成布局提示
        layout_hints = generate_layout_hints(nodes, edges, focus_node_id)

        # 4. 统计信息
        statistics = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "concept_nodes": sum(1 for n in nodes if n.is_concept),
            "design_methods": sum(1 for n in nodes if n.label == "DesignMethod"),
            "cases": sum(1 for n in nodes if n.label == "Case"),
            "sources": sum(1 for n in nodes if n.label == "Source"),
        }

        logger.info(f"[KG Subgraph] 返回节点: {len(nodes)}, 边: {len(edges)}")

        return SubgraphResponse(
            nodes=nodes,
            edges=edges,
            layout_hints=layout_hints,
            focus_node_id=focus_node_id,
            statistics=statistics,
            query=request.query
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[KG Subgraph] 提取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"提取子图失败: {str(e)}"
        )


@router.get("/kg/node/{node_id}", response_model=NodeDetailResponse, summary="获取节点详情")
async def get_node_detail(
    node_id: str,
    driver = Depends(get_neo4j_driver)
):
    """
    获取单个节点的详细信息，包括：
    - 节点基本信息
    - 关联的 chunk IDs
    - 关联的来源节点
    - 邻居节点（按关系类型分组）
    """
    try:
        import os
        database = os.getenv("NEO4J_DATABASE", "neo4j")

        with driver.session(database=database) as session:
            # 查询节点及其邻居
            result = session.run("""
                MATCH (n)
                WHERE id(n) = $node_id

                // 获取邻居节点（限制数量）
                OPTIONAL MATCH (n)-[r]-(neighbor)

                // 获取来源节点
                OPTIONAL MATCH (n)-[:MENTIONED_IN]->(source:Source)

                RETURN n,
                       collect(DISTINCT {node: neighbor, relationship: type(r), direction: CASE WHEN startNode(r) = n THEN 'OUT' ELSE 'IN' END}) as neighbors,
                       collect(DISTINCT source) as sources
                LIMIT 1
            """, node_id=int(node_id))

            record = result.single()
            if not record:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"节点不存在: {node_id}"
                )

            node = record["n"]
            node_data = NodeData(
                id=str(node.id),
                label=list(node.labels)[0],
                name=node.get("name", node.get("title", "Unknown")),
                is_concept=node.get("is_concept", False),
                properties=dict(node),
                layer=calculate_layer(list(node.labels)[0])
            )

            # 提取 chunk_ids
            chunk_ids = node.get("chunk_ids", [])

            # 提取来源节点
            sources = []
            for source in record["sources"]:
                if source:
                    sources.append({
                        "id": str(source.id),
                        "title": source.get("title"),
                        "source_type": source.get("source_type"),
                        "year": source.get("year"),
                    })

            # 提取邻居节点（按关系分组）
            neighbors_by_rel = {}
            for neighbor_info in record["neighbors"]:
                if neighbor_info and neighbor_info["node"]:
                    rel_type = neighbor_info["relationship"]
                    direction = neighbor_info["direction"]
                    key = f"{rel_type}_{direction}"

                    if key not in neighbors_by_rel:
                        neighbors_by_rel[key] = []

                    neighbor = neighbor_info["node"]
                    neighbors_by_rel[key].append(NodeData(
                        id=str(neighbor.id),
                        label=list(neighbor.labels)[0],
                        name=neighbor.get("name", neighbor.get("title", "Unknown")),
                        is_concept=neighbor.get("is_concept", False),
                        properties=dict(neighbor),
                        layer=calculate_layer(list(neighbor.labels)[0])
                    ))

            return NodeDetailResponse(
                node=node_data,
                related_chunks=chunk_ids,
                related_sources=sources,
                neighbors=neighbors_by_rel
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[KG Node Detail] 获取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取节点详情失败: {str(e)}"
        )


@router.get("/kg/statistics", response_model=GraphStatistics, summary="获取图谱统计")
async def get_graph_statistics(
    driver = Depends(get_neo4j_driver)
):
    """
    获取知识图谱的统计信息
    """
    try:
        import os
        database = os.getenv("NEO4J_DATABASE", "neo4j")

        with driver.session(database=database) as session:
            # 统计节点和关系数量
            result = session.run("""
                // 总节点数
                MATCH (n)
                WITH count(n) as total_nodes

                // 总关系数
                MATCH ()-[r]->()
                WITH total_nodes, count(r) as total_rels

                // 按类型统计节点
                MATCH (n)
                WITH total_nodes, total_rels, labels(n)[0] as label
                RETURN total_nodes,
                       total_rels,
                       label,
                       count(*) as count
            """)

            total_nodes = 0
            total_rels = 0
            node_type_counts = {}

            for record in result:
                total_nodes = record["total_nodes"]
                total_rels = record["total_rels"]
                node_type_counts[record["label"]] = record["count"]

            # 统计关系类型
            rel_result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(*) as count
            """)

            relationship_type_counts = {}
            for record in rel_result:
                relationship_type_counts[record["rel_type"]] = record["count"]

            # 特殊统计
            special_stats = session.run("""
                MATCH (c:Case) WITH count(c) as cases
                MATCH (d:DesignMethod) WITH cases, count(d) as methods
                MATCH (n) WHERE n.is_concept = true
                RETURN cases, methods, count(n) as concepts
            """).single()

            return GraphStatistics(
                total_nodes=total_nodes,
                total_relationships=total_rels,
                node_type_counts=node_type_counts,
                relationship_type_counts=relationship_type_counts,
                concept_node_count=special_stats["concepts"] if special_stats else 0,
                case_count=special_stats["cases"] if special_stats else 0,
                design_method_count=special_stats["methods"] if special_stats else 0
            )

    except Exception as e:
        logger.exception(f"[KG Statistics] 获取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取图谱统计失败: {str(e)}"
        )
