# app/services/graph_retriever.py
from __future__ import annotations
import asyncio
import os
import logging
from typing import List, Dict, Any

from neo4j import AsyncGraphDatabase, GraphDatabase
from backend.env_loader import load_dotenv

# 关系类型限制：
# - 设置为具体列表时，仅沿这些关系扩展（例如: ["CONTAINS", "LOCATED_IN"]）
# - 设置为 None 时，表示允许所有关系类型（更通用，兼容自定义图谱）
ALLOWED_TYPES = None  # 默认放开，避免因关系类型不一致而查不到结果

def _normalize_text(s) -> str:
    """
    规范化文本（统一转小写并去除空白）

    [FIX 2025-12-09] 支持处理列表输入，防止 'list' object has no attribute 'strip' 错误
    """
    # 如果是列表，取第一个元素
    if isinstance(s, list):
        s = s[0] if s else ""
    return (s or "").strip().lower()

def _is_reasonable_match(query: str, name: str, ratio_threshold: float = 0.2) -> bool:
    """
    仅当较长字符串包含较短字符串，且占比达到阈值时，认为匹配合理。
    ✅ [FIX 2025-11-19] 降低阈值从0.6到0.2，避免过度过滤
    例：'导向' vs '标识导向系统设置' -> 2/8 = 0.25 >= 0.2，认为命中。
    """
    q = _normalize_text(query)
    n = _normalize_text(name)
    if not q or not n:
        return False
    if q == n:
        return True
    if n in q and len(n) >= 2:
        return (len(n) / max(len(q), 1)) >= ratio_threshold
    if q in n and len(q) >= 2:
        return (len(q) / max(len(n), 1)) >= ratio_threshold
    return False

class GraphRetriever:
    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None, database: str | None = None):
        load_dotenv()
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.database = database or os.getenv("NEO4J_DATABASE", None)
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self._ensure_fulltext_index()

    def close(self):
        self.driver.close()

    # 确保全文索引存在
    def _ensure_fulltext_index(self):
        # 统一全文索引，覆盖英文与常见中文标签；不存在即创建
        # TODO: 未来新增的节点标签或可搜索属性，需同步更新下方的全文索引定义
        cypher = (
            "CREATE FULLTEXT INDEX unified_node_fulltext IF NOT EXISTS "
            "FOR (n:Hospital|Department|FunctionalArea|Space|SpaceArea|Entity|`空间`|`科室`|`技术指标`) "
            "ON EACH [n.name, n.slug]"
        )
        with self.driver.session(database=self.database) as s:
            try:
                s.run(cypher)
            except Exception as e:
                logging.warning(f"Create fulltext index failed (maybe exists or schema differs): {e}")

    # 1) 关键词检索节点（优先全文索引，回退包含匹配）
    def search_nodes(self, query: str, k: int = 10) -> List[Dict[str, Any]]:
        # 1) 优先使用统一全文索引；兼容旧索引名
        index_names = [
            "unified_node_fulltext",
            "node_fulltext",
            "node_fulltext_entity",
            "node_fulltext_cn",
        ]
        min_score = float(os.getenv("GRAPH_FULLTEXT_MIN_SCORE", "0.55"))
        with self.driver.session(database=self.database) as s:
            for idx in index_names:
                try:
                    recs = s.run(
                        """
                        CALL db.index.fulltext.queryNodes($idx, $q) YIELD node, score
                        OPTIONAL MATCH (node)-[:REFERENCES|MENTIONED_IN|BELONGS_TO]->(source:Source)
                        WITH node, score, collect(DISTINCT source.title) AS source_titles
                        RETURN labels(node)[0] AS label,
                               coalesce(node.id, node.slug) AS slug,
                               coalesce(node.title, node.name, node.id, node.slug) AS name,
                               score,
                               CASE
                                   WHEN coalesce(node.source_document, '') <> '' THEN node.source_document
                                   WHEN size(source_titles) > 0 THEN source_titles[0]
                                   WHEN coalesce(node.doc_title, '') <> '' THEN node.doc_title
                                   ELSE 'unknown'
                               END AS source_document,
                               source_titles AS source_documents
                        ORDER BY score DESC LIMIT $k
                        """,
                        idx=idx, q=query, k=k,
                    ).data()
                    if recs and (recs[0].get("score", 0) >= min_score):
                        filtered = [
                            r for r in recs
                            if _is_reasonable_match(query, r.get("name") or r.get("slug") or "")
                        ]
                        if filtered:
                            return filtered
                except Exception as e:
                    logging.info(f"Fulltext search via {idx} failed, fallback next. Error: {e}")

            # 2) 回退：使用子查询确保ORDER BY在聚合前生效
            # ✅ [FIX 2025-11-19] 方案3：拆分查询和聚合，先排序后追踪来源
            try:
                recs = s.run(
                    """
                    CALL {
                        MATCH (n)
                        WHERE NOT n:Source
                          AND (toLower(coalesce(n.title, n.name, '')) CONTAINS toLower($q)
                           OR toLower(coalesce(n.id, n.slug, '')) CONTAINS toLower($q))
                        RETURN n
                        ORDER BY CASE labels(n)[0]
                                   WHEN 'DesignMethod' THEN 1
                                   WHEN 'FunctionalZone' THEN 2
                                   WHEN 'MedicalEquipment' THEN 3
                                   WHEN 'Space' THEN 4
                                   ELSE 5
                                 END
                        LIMIT $k
                    }
                    OPTIONAL MATCH (n)-[:REFERENCES|MENTIONED_IN|BELONGS_TO]->(source:Source)
                    WITH n, collect(DISTINCT source.title) AS source_titles
                    RETURN labels(n)[0] AS label,
                           coalesce(n.id, n.slug) AS slug,
                           coalesce(n.title, n.name, n.id, n.slug) AS name,
                           1.0 AS score,
                           CASE
                               WHEN n.source_document IS NOT NULL AND n.source_document <> '' THEN n.source_document
                               WHEN size(source_titles) > 0 THEN source_titles[0]
                               WHEN n.doc_title IS NOT NULL AND n.doc_title <> '' THEN n.doc_title
                               WHEN n.doc_id IS NOT NULL AND n.doc_id <> '' THEN n.doc_id
                               ELSE 'unknown'
                           END AS source_document,
                           source_titles AS source_documents
                    """,
                    q=query, k=k,
                ).data()
                filtered = [
                    r for r in recs
                    if _is_reasonable_match(query, r.get("name") or r.get("slug") or "")
                ]
                return filtered
            except Exception as e:
                logging.warning(f"Fallback contains search failed: {e}")
                return []

    # 2) 邻域检索：从命中的节点出发，扩展1~2跳
    def expand_neighborhood(self, slugs: List[str], depth: int = 2, k_edges: int = 200) -> List[Dict[str, Any]]:
        # 支持 slug 缺失时用 name 作为标识（统一转小写匹配），动态关系模式（DRY）
        ids = [x for x in slugs if x]
        ids_lower = [x.lower() for x in ids]
        rel_pattern = f":{'|'.join(ALLOWED_TYPES)}" if ALLOWED_TYPES else ""

        cypher = f"""
        MATCH (n)
        WHERE ((coalesce(n.id, n.slug) IS NOT NULL AND coalesce(n.id, n.slug) IN $ids)
            OR toLower(coalesce(n.title, n.name, "")) IN $ids_lower)
        MATCH p = (n)-[r{rel_pattern}*1..{depth}]-(m)
        WITH relationships(p) AS rs, nodes(p) AS ns
        UNWIND rs AS e
        WITH DISTINCT startNode(e) AS a, type(e) AS t, endNode(e) AS b
        OPTIONAL MATCH (a)-[:DERIVED_FROM|COMPLIES_WITH|REFERENCES]->(source_a:Source)
        OPTIONAL MATCH (b)-[:DERIVED_FROM|COMPLIES_WITH|REFERENCES]->(source_b:Source)
        WITH a, b, t,
             collect(DISTINCT source_a.title) AS a_sources,
             collect(DISTINCT source_b.title) AS b_sources
        RETURN labels(a)[0] AS a_label,
               coalesce(a.id, a.slug) AS a_slug,
               coalesce(a.title, a.name, a.id, a.slug) AS a_name,
               CASE
                   WHEN a.source_document IS NOT NULL AND a.source_document <> '' THEN a.source_document
                   WHEN size(a_sources) > 0 THEN a_sources[0]
                   WHEN a.doc_title IS NOT NULL AND a.doc_title <> '' THEN a.doc_title
                   WHEN a.doc_id IS NOT NULL AND a.doc_id <> '' THEN a.doc_id
                   ELSE 'unknown'
               END AS a_source_document,
               a_sources AS a_source_documents,
               t AS rel_type,
               labels(b)[0] AS b_label,
               coalesce(b.id, b.slug) AS b_slug,
               coalesce(b.title, b.name, b.id, b.slug) AS b_name,
               CASE
                   WHEN b.source_document IS NOT NULL AND b.source_document <> '' THEN b.source_document
                   WHEN size(b_sources) > 0 THEN b_sources[0]
                   WHEN b.doc_title IS NOT NULL AND b.doc_title <> '' THEN b.doc_title
                   WHEN b.doc_id IS NOT NULL AND b.doc_id <> '' THEN b.doc_id
                   ELSE 'unknown'
               END AS b_source_document,
               b_sources AS b_source_documents
        LIMIT $k
        """
        with self.driver.session(database=self.database) as s:
            try:
                return s.run(cypher, ids=ids, ids_lower=ids_lower, k=k_edges).data()
            except Exception as e:
                logging.warning(f"expand_neighborhood failed: {e}")
                return []

    # 3) 约束关系上的最短路径（用于“X 到 Y 之间有何包含关系”）
    def shortest_path(self, source_slug: str, target_slug: str, max_len: int = 6) -> List[Dict[str, Any]]:
        # 兼容 slug 缺失：支持按 name 精确匹配（大小写不敏感），动态关系模式（DRY）
        edge = f":{'|'.join(ALLOWED_TYPES)}*..{max_len}" if ALLOWED_TYPES else f"*..{max_len}"
        cypher = f"""
        MATCH (a), (b)
        WHERE (((a.slug IS NOT NULL AND a.slug = $s) OR toLower(coalesce(a.name, "")) = toLower($s))
          AND ((b.slug IS NOT NULL AND b.slug = $t) OR toLower(coalesce(b.name, "")) = toLower($t)))
        MATCH p = shortestPath((a)-[{edge}]-(b))
        WITH p, nodes(p) AS ns, relationships(p) AS rs
        RETURN [n IN ns | {{label:labels(n)[0], slug:n.slug, name:coalesce(n.name,n.slug)}}] AS nodes,
               [r IN rs | {{type:type(r)}}] AS rels
        """
        with self.driver.session(database=self.database) as s:
            try:
                rec = s.run(cypher, s=source_slug, t=target_slug).single()
                return rec and rec.data() or []
            except Exception as e:
                logging.warning(f"shortest_path failed: {e}")
                return []

    # 4) 汇总文本上下文（给 LLM）
    def to_text_context(self, hits: List[Dict[str, Any]]) -> str:
        lines = []
        for h in hits:
            lines.append(f"{h['a_name']} ({h['a_label']}:{h['a_slug']}) -[{h['rel_type']}]-> {h['b_name']} ({h['b_label']}:{h['b_slug']})")
        return "\n".join(lines)

    def to_json_context(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        直接透传 expand_neighborhood 的边列表，提供给上层工具以 JSON 形式返回。
        字段包含：a_label, a_slug, a_name, rel_type, b_label, b_slug, b_name
        """
        return hits
    
    def list_neighbors(self, query: str, max_neighbors: int = 50) -> List[Dict[str, Any]]:
        """
        返回与指定概念直接相连的邻居清单（1跳）。
        输出字段：neighbor_label, neighbor_slug, neighbor_name, rel_type, direction
        """
        rel_pattern = f":{'|'.join(ALLOWED_TYPES)}" if ALLOWED_TYPES else ""
        cypher = f"""
        MATCH (n)
        WHERE toLower(coalesce(n.name,'')) CONTAINS toLower($q)
           OR toLower(coalesce(n.slug,'')) CONTAINS toLower($q)
        WITH n LIMIT 1
        MATCH (n)-[r{rel_pattern}]-(m)
        RETURN
            labels(m)[0] AS neighbor_label,
            m.slug        AS neighbor_slug,
            coalesce(m.name, m.slug) AS neighbor_name,
            type(r)       AS rel_type,
            CASE WHEN (n)-[r]->(m) THEN 'out' WHEN (n)<-[r]-(m) THEN 'in' ELSE 'undirected' END AS direction
        LIMIT $k
        """
        with self.driver.session(database=self.database) as s:
            try:
                return s.run(cypher, q=query, k=max_neighbors).data()
            except Exception as e:
                logging.warning(f"list_neighbors failed: {e}")
                return []

    def search_related_specs(self, query: str | None = None, seeds: List[str] | None = None, limit: int = 5) -> List[Dict[str, Any]]:
        """
        动态检索与当前查询或种子节点相关的设计规范类节点。
        兼容多种标签：DesignSpec / 规范 / 标准 / 政策；兼容内容属性：content/description/text。
        优先使用 seeds（与命中概念的关联规范），否则回退基于 query 的模糊匹配。
        """
        with self.driver.session(database=self.database) as s:
            # 1) 基于种子概念的关联规范（优先）
            if seeds:
                try:
                    cypher_seeds = """
                    WITH $seeds AS seeds
                    UNWIND seeds AS sid
                    MATCH (c)
                    WHERE (c.slug IS NOT NULL AND c.slug = sid)
                       OR toLower(coalesce(c.name,'')) = toLower(sid)
                    MATCH (d)--(c)
                    WHERE any(lbl IN labels(d) WHERE lbl IN ['DesignSpec','规范','标准','政策'])
                    RETURN DISTINCT
                        coalesce(d.name, d.slug) AS name,
                        coalesce(d['content'], d['description'], d['text'], '') AS content
                    LIMIT $limit
                    """
                    recs = s.run(cypher_seeds, seeds=seeds, limit=limit).data()
                    if recs:
                        return recs
                except Exception as e:
                    logging.warning(f"search_related_specs by seeds failed: {e}")

            # 2) 回退：基于原始 query 的模糊匹配
            try:
                cypher_q = """
                WITH toLower($q) AS q
                MATCH (d)
                WHERE any(lbl IN labels(d) WHERE lbl IN ['DesignSpec','规范','标准','政策'])
                  AND (
                    toLower(coalesce(d.name,'')) CONTAINS q OR
                    toLower(coalesce(d.slug,'')) CONTAINS q OR
                    toLower(coalesce(d['content'], d['description'], d['text'], '')) CONTAINS q
                  )
                RETURN DISTINCT
                    coalesce(d.name, d.slug) AS name,
                    coalesce(d['content'], d['description'], d['text'], '') AS content
                LIMIT $limit
                """
                return s.run(cypher_q, q=query or "", limit=limit).data()
            except Exception as e:
                logging.warning(f"search_related_specs by query failed: {e}")
                return []

    # 5) 多跳推理路径查询（2-5跳，用于复杂推理）
    def multi_hop_reasoning(self, start_query: str, end_query: str = None, min_hops: int = 2, max_hops: int = 5) -> List[Dict[str, Any]]:
        """
        查找从起始概念到目标概念的多跳推理路径
        
        Args:
            start_query: 起始概念关键词
            end_query: 目标概念关键词（可选，如果为None则返回所有多跳路径）
            min_hops: 最小跳数
            max_hops: 最大跳数
        
        Returns:
            推理路径列表，每个路径包含nodes和relationships
        """
        with self.driver.session(database=self.database) as s:
            # 先找到起始节点
            start_nodes = s.run("""
                MATCH (n)
                WHERE toLower(n.name) CONTAINS toLower($q) OR toLower(n.slug) CONTAINS toLower($q)
                RETURN n.slug AS slug
                LIMIT 3
            """, q=start_query).data()
            
            if not start_nodes:
                return []
            
            start_slugs = [n['slug'] for n in start_nodes]
            
            # 如果指定了目标概念
            if end_query:
                end_nodes = s.run("""
                    MATCH (n)
                    WHERE toLower(n.name) CONTAINS toLower($q) OR toLower(n.slug) CONTAINS toLower($q)
                    RETURN n.slug AS slug
                    LIMIT 3
                """, q=end_query).data()
                
                if not end_nodes:
                    return []
                
                end_slugs = [n['slug'] for n in end_nodes]
                
                # 查找所有可能的路径
                cypher = f"""
                MATCH (start) WHERE start.slug IN $start_slugs
                MATCH (end) WHERE end.slug IN $end_slugs
                MATCH path = (start)-[*{min_hops}..{max_hops}]-(end)
                WITH path, nodes(path) AS path_nodes, relationships(path) AS path_rels
                RETURN 
                    [n IN path_nodes | {{label: labels(n)[0], slug: n.slug, name: coalesce(n.name, n.slug)}}] AS nodes,
                    [r IN path_rels | {{type: type(r)}}] AS rels,
                    length(path) AS hops
                ORDER BY hops
                LIMIT 10
                """
                results = s.run(cypher, start_slugs=start_slugs, end_slugs=end_slugs).data()
            else:
                # 只从起始节点扩展
                cypher = f"""
                MATCH (start) WHERE start.slug IN $start_slugs
                MATCH path = (start)-[*{min_hops}..{max_hops}]-(end)
                WITH path, nodes(path) AS path_nodes, relationships(path) AS path_rels
                RETURN 
                    [n IN path_nodes | {{label: labels(n)[0], slug: n.slug, name: coalesce(n.name, n.slug)}}] AS nodes,
                    [r IN path_rels | {{type: type(r)}}] AS rels,
                    length(path) AS hops
                ORDER BY hops
                LIMIT 20
                """
                results = s.run(cypher, start_slugs=start_slugs).data()
            
            return results
    
    # 6) 查找关联实体（通过共同邻居发现潜在关联）
    def find_related_entities(self, query: str, min_common_neighbors: int = 2) -> List[Dict[str, Any]]:
        """
        通过共同邻居发现与查询概念潜在相关的实体
        
        Args:
            query: 查询概念
            min_common_neighbors: 最小共同邻居数
        
        Returns:
            相关实体列表，包含共同邻居数量
        """
        with self.driver.session(database=self.database) as s:
            # 找到查询节点
            query_nodes = s.run("""
                MATCH (n)
                WHERE toLower(n.name) CONTAINS toLower($q) OR toLower(n.slug) CONTAINS toLower($q)
                RETURN n.slug AS slug
                LIMIT 3
            """, q=query).data()
            
            if not query_nodes:
                return []
            
            query_slugs = [n['slug'] for n in query_nodes]
            
            # 通过共同邻居找相关实体
            cypher = """
            MATCH (query) WHERE query.slug IN $query_slugs
            MATCH (query)--(common)--(related)
            WHERE query <> related
            WITH related, count(DISTINCT common) AS common_count
            WHERE common_count >= $min_common
            RETURN 
                labels(related)[0] AS label,
                related.slug AS slug,
                coalesce(related.name, related.slug) AS name,
                common_count
            ORDER BY common_count DESC
            LIMIT 20
            """
            
            return s.run(cypher, query_slugs=query_slugs, min_common=min_common_neighbors).data()


class AsyncGraphRetriever:
    """Neo4j 异步检索器，避免在线程池调用阻塞的官方驱动。"""

    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None, database: str | None = None):
        load_dotenv()
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.database = database or os.getenv("NEO4J_DATABASE", None)
        self.driver = AsyncGraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self._index_lock = asyncio.Lock()
        self._index_ready = False

    async def close(self) -> None:
        if self.driver:
            await self.driver.close()

    async def _ensure_fulltext_index(self) -> None:
        if self._index_ready:
            return
        async with self._index_lock:
            if self._index_ready:
                return
            cypher = (
                "CREATE FULLTEXT INDEX unified_node_fulltext IF NOT EXISTS "
                "FOR (n:Hospital|Department|FunctionalArea|Space|SpaceArea|Entity|`空间`|`科室`|`技术指标`) "
                "ON EACH [n.name, n.slug]"
            )
            try:
                async with self.driver.session(database=self.database) as session:
                    await (await session.run(cypher)).consume()
            except Exception as e:  # pragma: no cover - 仅记录日志
                logging.warning("Async index ensure failed: %s", e)
            self._index_ready = True

    async def search_nodes(self, query: str, k: int = 10) -> List[Dict[str, Any]]:
        await self._ensure_fulltext_index()
        index_names = [
            "unified_node_fulltext",
            "node_fulltext",
            "node_fulltext_entity",
            "node_fulltext_cn",
        ]
        min_score = float(os.getenv("GRAPH_FULLTEXT_MIN_SCORE", "0.55"))
        async with self.driver.session(database=self.database) as session:
            # ✅ [FIX 2025-11-19] 使用子查询确保ORDER BY在聚合前生效
            # ✅ [CRITICAL] 方案3：拆分查询和聚合，先排序后追踪来源
            # ✅ [FIX 2025-11-19] 修复字段映射：实际字段是title/id，而非name/slug
            try:
                direct_result = await session.run(
                    """
                    CALL {
                        MATCH (n)
                        WHERE NOT n:Source
                          AND (toLower(coalesce(n.title, '')) CONTAINS toLower($q)
                           OR toLower(coalesce(n.id, '')) CONTAINS toLower($q)
                           OR toLower($q) CONTAINS toLower(coalesce(n.title, ''))
                           OR toLower($q) CONTAINS toLower(coalesce(n.id, '')))
                        RETURN n
                        ORDER BY CASE labels(n)[0]
                                   WHEN 'DesignMethod' THEN 1
                                   WHEN 'FunctionalZone' THEN 2
                                   WHEN 'MedicalEquipment' THEN 3
                                   WHEN 'Space' THEN 4
                                   ELSE 5
                                 END
                        LIMIT $k
                    }
                    OPTIONAL MATCH (n)-[:REFERENCES|MENTIONED_IN|BELONGS_TO]->(source:Source)
                    WITH n, collect(DISTINCT source.title) AS source_titles
                    RETURN labels(n)[0] AS label,
                           coalesce(n.id, n.slug) AS slug,
                           coalesce(n.title, n.id, n.slug) AS name,
                           1.0 AS score,
                           CASE
                               WHEN n.source_document IS NOT NULL AND n.source_document <> '' THEN n.source_document
                               WHEN size(source_titles) > 0 THEN source_titles[0]
                               WHEN n.doc_title IS NOT NULL AND n.doc_title <> '' THEN n.doc_title
                               WHEN n.doc_id IS NOT NULL AND n.doc_id <> '' THEN n.doc_id
                               ELSE 'unknown'
                           END AS source_document,
                           source_titles AS source_documents
                    """,
                    q=query,
                    k=k,
                )
                direct_recs = await direct_result.data()

                # 过滤合理匹配
                filtered_direct = [
                    r for r in direct_recs
                    if _is_reasonable_match(query, r.get("name") or r.get("slug") or "")
                ]

                # 如果直接查询有结果，直接返回
                if filtered_direct:
                    logging.info("[AsyncGraphRetriever] 直接查询命中 %d 个节点", len(filtered_direct))
                    return filtered_direct

            except Exception as e:
                logging.info("Direct query failed: %s", e)

            # 如果直接查询无结果，再尝试全文索引
            for idx in index_names:
                try:
                    result = await session.run(
                        """
                        CALL db.index.fulltext.queryNodes($idx, $q) YIELD node, score
                        OPTIONAL MATCH (node)-[:DERIVED_FROM|COMPLIES_WITH|REFERENCES]->(source:Source)
                        WITH node, score, collect(DISTINCT source.title) AS source_titles
                        RETURN labels(node)[0] AS label,
                               coalesce(node.id, node.slug) AS slug,
                               coalesce(node.title, node.name, node.id, node.slug) AS name,
                               score,
                               CASE
                                   WHEN coalesce(node.source_document, '') <> '' THEN node.source_document
                                   WHEN size(source_titles) > 0 THEN source_titles[0]
                                   WHEN coalesce(node.doc_title, '') <> '' THEN node.doc_title
                                   ELSE 'unknown'
                               END AS source_document,
                               source_titles AS source_documents
                        ORDER BY score DESC LIMIT $k
                        """,
                        idx=idx,
                        q=query,
                        k=k,
                    )
                    recs = await result.data()
                    if recs and recs[0].get("score", 0) >= min_score:
                        filtered = [
                            r for r in recs
                            if _is_reasonable_match(query, r.get("name") or r.get("slug") or "")
                        ]
                        if filtered:
                            return filtered
                except Exception as e:
                    logging.info("Async fulltext search via %s failed: %s", idx, e)

            try:
                # ✅ [FIX 2025-11-19] 优化回退查询，使用子查询确保ORDER BY生效
                # ✅ [FIX 2025-11-19] 修复字段映射：实际字段是title/id，而非name/slug
                fallback = await session.run(
                    """
                    CALL {
                        MATCH (n)
                        WHERE NOT n:Source
                          AND (toLower(coalesce(n.title, '')) CONTAINS toLower($q)
                           OR toLower(coalesce(n.id, '')) CONTAINS toLower($q)
                           OR toLower($q) CONTAINS toLower(coalesce(n.title, ''))
                           OR toLower($q) CONTAINS toLower(coalesce(n.id, '')))
                        RETURN n
                        ORDER BY CASE labels(n)[0]
                                   WHEN 'DesignMethod' THEN 1
                                   WHEN 'FunctionalZone' THEN 2
                                   WHEN 'MedicalEquipment' THEN 3
                                   WHEN 'Space' THEN 4
                                   ELSE 5
                                 END
                        LIMIT $k
                    }
                    OPTIONAL MATCH (n)-[:REFERENCES|MENTIONED_IN|BELONGS_TO]->(source:Source)
                    WITH n, collect(DISTINCT source.title) AS source_titles
                    RETURN labels(n)[0] AS label,
                           coalesce(n.id, n.slug) AS slug,
                           coalesce(n.title, n.id, n.slug) AS name,
                           1.0 AS score,
                           CASE
                               WHEN n.source_document IS NOT NULL AND n.source_document <> '' THEN n.source_document
                               WHEN size(source_titles) > 0 THEN source_titles[0]
                               WHEN n.doc_title IS NOT NULL AND n.doc_title <> '' THEN n.doc_title
                               WHEN n.doc_id IS NOT NULL AND n.doc_id <> '' THEN n.doc_id
                               ELSE 'unknown'
                           END AS source_document,
                           source_titles AS source_documents
                    """,
                    q=query,
                    k=k,
                )
                recs = await fallback.data()
                filtered = [
                    r for r in recs
                    if _is_reasonable_match(query, r.get("name") or r.get("slug") or "")
                ]
                logging.info("[AsyncGraphRetriever] 回退查询命中 %d 个节点", len(filtered))
                return filtered
            except Exception as e:
                logging.warning("Async fallback contains search failed: %s", e)
                return []

    async def expand_neighborhood(self, slugs: List[str], depth: int = 2, k_edges: int = 200) -> List[Dict[str, Any]]:
        ids = [x for x in slugs if x]
        ids_lower = [x.lower() for x in ids]
        rel_pattern = f":{'|'.join(ALLOWED_TYPES)}" if ALLOWED_TYPES else ""
        cypher = f"""
        MATCH (n)
        WHERE ((coalesce(n.id, n.slug) IS NOT NULL AND coalesce(n.id, n.slug) IN $ids)
            OR toLower(coalesce(n.title, n.name, "")) IN $ids_lower)
        MATCH p = (n)-[r{rel_pattern}*1..{depth}]-(m)
        WITH relationships(p) AS rs, nodes(p) AS ns
        UNWIND rs AS e
        WITH DISTINCT startNode(e) AS a, type(e) AS t, endNode(e) AS b
        OPTIONAL MATCH (a)-[:DERIVED_FROM|COMPLIES_WITH|REFERENCES]->(source_a:Source)
        OPTIONAL MATCH (b)-[:DERIVED_FROM|COMPLIES_WITH|REFERENCES]->(source_b:Source)
        WITH a, b, t,
             collect(DISTINCT source_a.title) AS a_sources,
             collect(DISTINCT source_b.title) AS b_sources
        RETURN labels(a)[0] AS a_label,
               coalesce(a.id, a.slug) AS a_slug,
               coalesce(a.title, a.name, a.id, a.slug) AS a_name,
               CASE
                   WHEN a.source_document IS NOT NULL AND a.source_document <> '' THEN a.source_document
                   WHEN size(a_sources) > 0 THEN a_sources[0]
                   WHEN a.doc_title IS NOT NULL AND a.doc_title <> '' THEN a.doc_title
                   WHEN a.doc_id IS NOT NULL AND a.doc_id <> '' THEN a.doc_id
                   ELSE 'unknown'
               END AS a_source_document,
               a_sources AS a_source_documents,
               t AS rel_type,
               labels(b)[0] AS b_label,
               coalesce(b.id, b.slug) AS b_slug,
               coalesce(b.title, b.name, b.id, b.slug) AS b_name,
               CASE
                   WHEN b.source_document IS NOT NULL AND b.source_document <> '' THEN b.source_document
                   WHEN size(b_sources) > 0 THEN b_sources[0]
                   WHEN b.doc_title IS NOT NULL AND b.doc_title <> '' THEN b.doc_title
                   WHEN b.doc_id IS NOT NULL AND b.doc_id <> '' THEN b.doc_id
                   ELSE 'unknown'
               END AS b_source_document,
               b_sources AS b_source_documents
        LIMIT $k
        """
        async with self.driver.session(database=self.database) as session:
            try:
                result = await session.run(cypher, ids=ids, ids_lower=ids_lower, k=k_edges)
                return await result.data()
            except Exception as e:
                logging.warning("Async expand_neighborhood failed: %s", e)
                return []

    async def shortest_path(self, source_slug: str, target_slug: str, max_len: int = 6) -> List[Dict[str, Any]]:
        edge = f":{'|'.join(ALLOWED_TYPES)}*..{max_len}" if ALLOWED_TYPES else f"*..{max_len}"
        cypher = f"""
        MATCH (a), (b)
        WHERE (((a.slug IS NOT NULL AND a.slug = $s) OR toLower(coalesce(a.name, "")) = toLower($s))
          AND ((b.slug IS NOT NULL AND b.slug = $t) OR toLower(coalesce(b.name, "")) = toLower($t)))
        MATCH p = shortestPath((a)-[{edge}]-(b))
        WITH p, nodes(p) AS ns, relationships(p) AS rs
        RETURN [n IN ns | {{label:labels(n)[0], slug:n.slug, name:coalesce(n.name,n.slug)}}] AS nodes,
               [r IN rs | {{type:type(r)}}] AS rels
        """
        async with self.driver.session(database=self.database) as session:
            try:
                result = await session.run(cypher, s=source_slug, t=target_slug)
                record = await result.single()
                return record and record.data() or []
            except Exception as e:
                logging.warning("Async shortest_path failed: %s", e)
                return []

    def to_text_context(self, hits: List[Dict[str, Any]]) -> str:
        lines = []
        for h in hits:
            lines.append(f"{h['a_name']} ({h['a_label']}:{h['a_slug']}) -[{h['rel_type']}]-> {h['b_name']} ({h['b_label']}:{h['b_slug']})")
        return "\n".join(lines)

    def to_json_context(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return hits

    async def list_neighbors(self, query: str, max_neighbors: int = 50) -> List[Dict[str, Any]]:
        rel_pattern = f":{'|'.join(ALLOWED_TYPES)}" if ALLOWED_TYPES else ""
        cypher = f"""
        MATCH (n)
        WHERE toLower(coalesce(n.name,'')) CONTAINS toLower($q)
           OR toLower(coalesce(n.slug,'')) CONTAINS toLower($q)
        WITH n LIMIT 1
        MATCH (n)-[r{rel_pattern}]-(m)
        RETURN
            labels(m)[0] AS neighbor_label,
            m.slug        AS neighbor_slug,
            coalesce(m.name, m.slug) AS neighbor_name,
            type(r)       AS rel_type,
            CASE WHEN (n)-[r]->(m) THEN 'out' WHEN (n)<-[r]-(m) THEN 'in' ELSE 'undirected' END AS direction
        LIMIT $k
        """
        async with self.driver.session(database=self.database) as session:
            try:
                result = await session.run(cypher, q=query, k=max_neighbors)
                return await result.data()
            except Exception as e:
                logging.warning("Async list_neighbors failed: %s", e)
                return []

    async def search_related_specs(self, query: str | None = None, seeds: List[str] | None = None, limit: int = 5) -> List[Dict[str, Any]]:
        async with self.driver.session(database=self.database) as session:
            if seeds:
                try:
                    result = await session.run(
                        """
                        WITH $seeds AS seeds
                        UNWIND seeds AS sid
                        MATCH (c)
                        WHERE (c.slug IS NOT NULL AND c.slug = sid)
                           OR toLower(coalesce(c.name,'')) = toLower(sid)
                        MATCH (d)--(c)
                        WHERE any(lbl IN labels(d) WHERE lbl IN ['DesignSpec','规范','标准','政策'])
                        RETURN DISTINCT
                            coalesce(d.name, d.slug) AS name,
                            coalesce(d['content'], d['description'], d['text'], '') AS content
                        LIMIT $limit
                        """,
                        seeds=seeds,
                        limit=limit,
                    )
                    recs = await result.data()
                    if recs:
                        return recs
                except Exception as e:
                    logging.warning("Async search_related_specs by seeds failed: %s", e)
            try:
                fallback = await session.run(
                    """
                    WITH toLower($q) AS q
                    MATCH (d)
                    WHERE any(lbl IN labels(d) WHERE lbl IN ['DesignSpec','规范','标准','政策'])
                      AND (
                        toLower(coalesce(d.name,'')) CONTAINS q OR
                        toLower(coalesce(d.slug,'')) CONTAINS q OR
                        toLower(coalesce(d['content'], d['description'], d['text'], '')) CONTAINS q
                      )
                    RETURN DISTINCT
                        coalesce(d.name, d.slug) AS name,
                        coalesce(d['content'], d['description'], d['text'], '') AS content
                    LIMIT $limit
                    """,
                    q=query or "",
                    limit=limit,
                )
                return await fallback.data()
            except Exception as e:
                logging.warning("Async search_related_specs by query failed: %s", e)
                return []

    async def multi_hop_reasoning(self, start_query: str, end_query: str | None = None, min_hops: int = 2, max_hops: int = 5) -> List[Dict[str, Any]]:
        async with self.driver.session(database=self.database) as session:
            start_nodes_result = await session.run(
                """
                MATCH (n)
                WHERE toLower(n.name) CONTAINS toLower($q) OR toLower(n.slug) CONTAINS toLower($q)
                RETURN n.slug AS slug
                LIMIT 3
                """,
                q=start_query,
            )
            start_nodes = await start_nodes_result.data()
            if not start_nodes:
                return []
            start_slugs = [n['slug'] for n in start_nodes]

            if end_query:
                end_nodes_result = await session.run(
                    """
                    MATCH (n)
                    WHERE toLower(n.name) CONTAINS toLower($q) OR toLower(n.slug) CONTAINS toLower($q)
                    RETURN n.slug AS slug
                    LIMIT 3
                    """,
                    q=end_query,
                )
                end_nodes = await end_nodes_result.data()
                if not end_nodes:
                    return []
                end_slugs = [n['slug'] for n in end_nodes]
                cypher = f"""
                MATCH (start) WHERE start.slug IN $start_slugs
                MATCH (end) WHERE end.slug IN $end_slugs
                MATCH path = (start)-[*{min_hops}..{max_hops}]-(end)
                WITH path, nodes(path) AS path_nodes, relationships(path) AS path_rels
                RETURN 
                    [n IN path_nodes | {{label: labels(n)[0], slug: n.slug, name: coalesce(n.name, n.slug)}}] AS nodes,
                    [r IN path_rels | {{type: type(r)}}] AS rels,
                    length(path) AS hops
                ORDER BY hops
                LIMIT 10
                """
                result = await session.run(cypher, start_slugs=start_slugs, end_slugs=end_slugs)
            else:
                cypher = f"""
                MATCH (start) WHERE start.slug IN $start_slugs
                MATCH path = (start)-[*{min_hops}..{max_hops}]-(end)
                WITH path, nodes(path) AS path_nodes, relationships(path) AS path_rels
                RETURN 
                    [n IN path_nodes | {{label: labels(n)[0], slug: n.slug, name: coalesce(n.name, n.slug)}}] AS nodes,
                    [r IN path_rels | {{type: type(r)}}] AS rels,
                    length(path) AS hops
                ORDER BY hops
                LIMIT 20
                """
                result = await session.run(cypher, start_slugs=start_slugs)
            return await result.data()

    async def find_related_entities(self, query: str, min_common_neighbors: int = 2) -> List[Dict[str, Any]]:
        async with self.driver.session(database=self.database) as session:
            query_nodes_result = await session.run(
                """
                MATCH (n)
                WHERE toLower(n.name) CONTAINS toLower($q) OR toLower(n.slug) CONTAINS toLower($q)
                RETURN n.slug AS slug
                LIMIT 3
                """,
                q=query,
            )
            query_nodes = await query_nodes_result.data()
            if not query_nodes:
                return []
            query_slugs = [n['slug'] for n in query_nodes]
            result = await session.run(
                """
                MATCH (query) WHERE query.slug IN $query_slugs
                MATCH (query)--(common)--(related)
                WHERE query <> related
                WITH related, count(DISTINCT common) AS common_count
                WHERE common_count >= $min_common
                RETURN 
                    labels(related)[0] AS label,
                    related.slug AS slug,
                    coalesce(related.name, related.slug) AS name,
                    common_count
                ORDER BY common_count DESC
                LIMIT 20
                """,
                query_slugs=query_slugs,
                min_common=min_common_neighbors,
            )
            return await result.data()


__all__ = ["GraphRetriever", "AsyncGraphRetriever"]
