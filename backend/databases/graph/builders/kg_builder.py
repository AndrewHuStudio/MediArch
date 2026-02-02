"""
医疗建筑知识图谱构建器

方案：图谱（Neo4j）+ 溯源（MongoDB）
- Neo4j：存储实体和关系（轻量级结构）+ 内嵌轻量属性
- MongoDB：原始文档和富媒体数据溯源

特性：
1. 属性不再创建独立节点，存储为实体properties
2. Milvus存储富内容向量，支持语义检索
3. 富媒体引用提取（图片、表格）
4. 支持智能体协同工作流程
"""

import hashlib
import json
import os
import re
import sys
import threading
import difflib
import itertools
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
import time

import nanoid
import networkx as nx
from dotenv import load_dotenv
from backend.databases.graph.optimization.name_normalizer import canonicalize, compose_scope_key, load_alias_map
from neo4j import GraphDatabase
from pymongo import MongoClient
from backend.databases.graph.utils.call_llm_api import LLMClient

from .relation_mapping import (
    normalize_relation,
    get_inverse_relation,
    classify_attribute_type
)

load_dotenv()


class MedicalKGBuilder:
    """医疗建筑知识图谱构建器（Neo4j 图谱构建）"""
    
    def __init__(
        self,
        schema_path: str = "backend/databases/graph/schemas/medical_architecture_3.json"
    ):
        """
        初始化构建器

        Args:
            schema_path: Schema文件路径
        """
        # 加载Schema（支持环境变量 KG_SCHEMA_PATH 覆盖）
        resolved_schema_path = os.getenv("KG_SCHEMA_PATH", schema_path)
        self.schema = self.load_schema(resolved_schema_path)
        # 同时支持 Labels / NodeConcepts 两种定义方式
        node_defs = self.schema.get("Labels") or self.schema.get("NodeConcepts") or []
        # 建立 概念↔标签 映射，供后续类型/关系校验与真实标签写入
        self.concept_to_label = {}
        self.label_to_concept = {}
        for node in node_defs or []:
            if not isinstance(node, dict):
                continue
            concept = node.get("concept") or node.get("type")
            label = node.get("label")
            if concept and label:
                self.concept_to_label[concept] = label
                self.label_to_concept[label] = concept
        self.allowed_entity_types = {
            node.get("concept") or node.get("type")
            for node in node_defs
            if isinstance(node, dict) and (node.get("concept") or node.get("type"))
        }
        self.allowed_relation_types = {
            rel.get("name")
            for rel in self.schema.get("Relations", [])
            if isinstance(rel, dict) and rel.get("name")
        }
        # 类型到真实标签的映射（用于写入 Neo4j 实际标签）
        self.type_to_label = dict(self.concept_to_label)

        # 不同标签的节点属性白名单与主显示字段（name/title）
        # 兼容两种 schema：
        #  - Labels[].attributes 为对象（字典键即属性键）
        #  - NodeConcepts[].attributes 为属性键列表，需参考 AttributeDefinitions
        self.allowed_props_by_label = {}
        self.primary_name_key_by_label = {}
        attribute_defs = self.schema.get("AttributeDefinitions") or {}
        for node in node_defs or []:
            if not isinstance(node, dict):
                continue
            label = node.get("label")
            attrs = node.get("attributes")
            keys: set = set()
            if isinstance(attrs, dict):
                keys = set(attrs.keys())
            elif isinstance(attrs, list):
                keys = set([k for k in attrs if isinstance(k, str)])
            if label:
                self.allowed_props_by_label[label] = keys
                # 主显示字段：优先 name，其次 title；若都不在白名单但定义中存在，仍以 name 优先
                primary_key = "name" if "name" in keys else ("title" if "title" in keys else ("name" if "name" in attribute_defs else ("title" if "title" in attribute_defs else "name")))
                self.primary_name_key_by_label[label] = primary_key

        # 关系属性白名单 + 端点类型（使用中文概念进行校验，若给的是英文标签则反查为概念）
        self.allowed_rel_props_by_type = {}
        self.relation_constraints = {}
        for rel in self.schema.get("Relations", []) or []:
            if not isinstance(rel, dict) or not rel.get("name"):
                continue
            props = rel.get("properties") or {}
            self.allowed_rel_props_by_type[rel["name"]] = set(props.keys()) if isinstance(props, dict) else set()
            subj_types = rel.get("subjectTypes") or []
            obj_types = rel.get("objectTypes") or []
            # 将英文标签映射回中文概念；若本就是中文概念则保持原值
            subj_concepts = set()
            for t in subj_types:
                if t in self.label_to_concept:
                    subj_concepts.add(self.label_to_concept[t])
                else:
                    subj_concepts.add(t)
            obj_concepts = set()
            for t in obj_types:
                if t in self.label_to_concept:
                    obj_concepts.add(self.label_to_concept[t])
                else:
                    obj_concepts.add(t)
            self.relation_constraints[rel["name"]] = (subj_concepts, obj_concepts)

        # 控制是否保留节点上的 attributes 文本列表（默认不保留，避免污染节点属性）
        self.keep_attributes_list = os.getenv("KG_NODE_KEEP_ATTRIBUTES_LIST", "0").lower() in {"1", "true", "yes"}
        
        # 实体类型同义词归一化（向下兼容老抽取）
        self.type_synonyms = {
            "功能单元": "功能分区",
            # 可按需追加："功能区": "功能分区",
        }
        
        # 初始化NetworkX图（临时构建用）
        self.graph = nx.MultiDiGraph()
        self.node_counter = 0
        self.lock = threading.Lock()
        
        # 连接MongoDB
        self.mongo_client = MongoClient(os.getenv("MONGODB_URI"))
        self.db = self.mongo_client[os.getenv("MONGODB_DATABASE", "mediarch")]
        self.documents_collection = self.db.documents
        # 新增：直接读取标准化后的 chunks 集合，并设置 LLM 抽取结果缓存集合
        chunk_collection_name = os.getenv("MONGODB_CHUNK_COLLECTION", "mediarch_chunks")
        self.chunks_collection = self.db.get_collection(chunk_collection_name)
        self.extractions_collection = self.db.get_collection("kg_extractions")
        
        # 连接Neo4j
        neo4j_uri = os.getenv("NEO4J_URI")
        neo4j_user = os.getenv("NEO4J_USER")
        neo4j_password = os.getenv("NEO4J_PASSWORD")
        self.neo4j_database = os.getenv("NEO4J_DATABASE", "neo4j")

        self.neo4j_driver = GraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password)
        )
        
        # 初始化LLM Client（统一封装）
        # 支持两种环境变量前缀：KG_OPENAI_* 或 OPENAI_*（向下兼容）
        api_key = os.getenv("KG_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("KG_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        model = os.getenv("KG_OPENAI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

        self.llm_client = LLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self.llm_model = model
        # 是否将图片作为图节点进行回链（默认关闭，改为检索层召回）
        self.link_images = os.getenv("KG_LINK_IMAGES", "0").lower() in {"1", "true", "yes"}
        # 语义相似度消歧（候选建议）
        self.enable_semantic_fusion = os.getenv("KG_ENTITY_FUSION", "0").lower() in {"1", "true", "yes"}
        self.fusion_ratio = float(os.getenv("KG_FUSION_RATIO", "0.90"))
        self.fusion_max_pairs = int(os.getenv("KG_FUSION_MAX_PAIRS", "50"))
        # 自动别名链接（强匹配对写入别名边，不做节点合并）
        self.auto_alias_links = os.getenv("KG_FUSION_AUTO_ALIAS", "0").lower() in {"1", "true", "yes"}
        self.alias_min_score = float(os.getenv("KG_FUSION_ALIAS_MIN_SCORE", "0.96"))
        self.alias_min_overlap = int(os.getenv("KG_FUSION_ALIAS_MIN_REL_OVERLAP", "1"))
        self.alias_max_edges = int(os.getenv("KG_FUSION_ALIAS_MAX_EDGES", "200"))
        # 实体别名映射（用于名称归一，影响稳定ID生成）
        self.alias_map = load_alias_map(os.getenv("KG_ALIAS_PATH", ""))
        # 抽取版本号：由 schema 哈希 + LLM 型号 组成，用于幂等与缓存
        self.extraction_version = self._build_extraction_version()
        # Source 类型与 perspective 映射，基于资料目录/分类推断
        self.source_type_aliases = [
            ("规范标准", {"规范", "标准", "gb", "guideline", "code"}),
            ("政策文件", {"政策", "policy", "办法"}),
            ("学术文献", {"论文", "研究", "academic", "文献", "研究报告"}),
            ("图集书籍", {"图集", "图册", "图书", "图纸", "图解", "案例图"}),
            ("项目文档", {"项目", "方案", "案例", "report", "设计"}),
            ("会议纪要", {"会议", "纪要"}),
        ]
        self._source_node_cache: Dict[str, str] = {}

        # Schema 模式：strict（严格）/ soft（默认），soft 会对未知类型做启发式/LLM 归类
        self.schema_mode_soft = (os.getenv("KG_SCHEMA_MODE", "soft").lower() in {"soft", "0", "false"} and True) or (os.getenv("KG_SCHEMA_MODE", "soft").lower() == "soft")
        # 实体类型 LLM 兜底开关
        self.entity_type_llm_fallback = os.getenv("KG_ENTITY_TYPE_LLM_FALLBACK", "0").lower() in {"1", "true", "yes"}
        # 关系验证 LLM 兜底开关
        self.relation_llm_fallback = os.getenv("KG_RELATION_LLM_FALLBACK", "0").lower() in {"1", "true", "yes"}
        
        # 构建选项
        self.enable_cooccurrence_aug = os.getenv("KG_RELATION_COOC_AUG", "1").lower() in {"1", "true", "yes"}
        self.drop_uncertain_relations = os.getenv("KG_RELATION_DROP_UNCERTAIN", "0").lower() in {"1", "true", "yes"}

        cooccur_pairs_env = os.getenv("KG_COOCCUR_ALLOWED_TYPES", "").strip()
        if not cooccur_pairs_env:
            config_path = os.getenv(
                "KG_COOCCUR_ALLOWED_TYPES_FILE",
                "backend/databases/graph/config/cooccur_allowed_types.txt",
            )
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    cleaned_pairs: List[str] = []
                    for raw_line in fh:
                        line = raw_line.strip()
                        if not line or line.startswith("#"):
                            continue
                        cleaned_pairs.append(line)
                cooccur_pairs_env = ",".join(cleaned_pairs)
            except FileNotFoundError:
                print(f"[WARN] KGBuilder co-occur config file not found: {config_path}")
            except UnicodeDecodeError as exc:
                print(
                    f"[WARN] KGBuilder co-occur config decode error ({config_path}): {exc}"
                )

        self.cooccur_allowed_pairs_env = cooccur_pairs_env
        self.cooccur_allowed_pairs: Set[Tuple[str, str]] = set()
        if cooccur_pairs_env:
            for item in [x for x in cooccur_pairs_env.split(",") if ":" in x]:
                a, b = item.split(":", 1)
                a = a.strip()
                b = b.strip()
                if a and b:
                    self.cooccur_allowed_pairs.add((a, b))
                    self.cooccur_allowed_pairs.add((b, a))
        self.cooccur_write_cooccur = os.getenv("KG_COOCCUR_WRITE_CO_OCCUR", "0").lower() in {"1", "true", "yes"}

        self.last_write_summary: Dict[str, Any] = {}

        print(f"[OK] KGBuilder initialized")
        print(f"  - Neo4j: Entities + Relations")
        print(f"  - Mongo chunks collection: {self.chunks_collection.name}")
        if self.enable_semantic_fusion:
            print("  - Entity fusion suggestions: Enabled")
            print(f"    * ratio>={self.fusion_ratio}, max_pairs={self.fusion_max_pairs}")
            if self.auto_alias_links:
                print("    * Auto alias links: Enabled")
                print(f"      - min_score>={self.alias_min_score}, min_overlap>={self.alias_min_overlap}, max_edges={self.alias_max_edges}")
            else:
                print("    * Auto alias links: Disabled (suggestions only)")
        else:
            print("  - Entity fusion suggestions: Disabled")
        # 保障索引与约束（幂等）
        self._ensure_indexes_and_constraints()

        # chunk 级索引缓存
        self.chunk_doc_map: Dict[str, str] = {}
        self.chunk_order_map: Dict[str, Any] = {}
        self._chunk_entity_index: Dict[str, List[str]] = defaultdict(list)
        self._chunk_sequence_counter = 0

        self.rebuild_strategy = self._determine_build_strategy()

        # 加载概念节点索引（用于去重）
        self.concept_nodes = self._load_concept_node_index()
        if self.concept_nodes:
            total_concepts = sum(len(v) for v in self.concept_nodes.values())
            print(f"[OK] Loaded {total_concepts} concept nodes from Neo4j")
        else:
            print(
                "[WARN] No concept nodes found. "
                "Please run seed_ontology_v2.py (recommended) or seed_ontology.py first."
            )

        # 扩展允许的关系类型，加入弱证据关系
        if isinstance(self.allowed_relation_types, set):
            self.allowed_relation_types.add("CO_OCCUR")

    def _load_concept_node_index(self) -> Dict[str, Dict[str, str]]:
        """
        从Neo4j加载概念节点索引

        Returns:
            {
                "Hospital": {"综合医院": "entity_xxx"},
                "DepartmentGroup": {"急诊部": "entity_yyy", ...},
                ...
            }
        """
        index = {}
        try:
            with self.neo4j_driver.session(database=self.neo4j_database) as session:
                result = session.run("""
                    MATCH (n) WHERE n.is_concept = true
                    RETURN labels(n)[0] AS label,
                           coalesce(n.name, n.title) AS name,
                           n.id AS node_id
                """)
                for record in result:
                    label = record["label"]
                    name = record["name"]
                    node_id = record.get("node_id")
                    if not label or not name or not node_id:
                        continue
                    node_id = str(node_id)

                    if label not in index:
                        index[label] = {}
                    # 用规范化名称作为 key，避免别名/同义词导致无法命中
                    concept_type = self.label_to_concept.get(label, label)
                    canonical_name = canonicalize(str(name), str(concept_type), self.alias_map)
                    index[label][canonical_name] = node_id
        except Exception as e:
            print(f"[WARN] Failed to load concept node index: {e}")

        return index

    def calculate_quality_score(self, entity_data: Dict[str, Any], entity_type: str) -> float:
        """
        计算实体质量评分

        Args:
            entity_data: 实体数据（包含properties）
            entity_type: 实体类型

        Returns:
            质量评分 (0-1)
        """
        score = 0.0
        props = entity_data.get("properties", {})
        description = entity_data.get("description", "")

        # 案例节点评分
        if entity_type == "案例" or entity_data.get("label") == "Case":
            # 1. 是否有图片/图纸 (+0.3)
            if props.get("has_media"):
                score += 0.3

            # 2. 是否有详细参数 (+0.3)
            if props.get("has_detailed_params"):
                score += 0.3

            # 3. 是否有创新点 (+0.2)
            if props.get("has_innovation"):
                score += 0.2

            # 4. 内容长度 (+0.2)
            content_length = props.get("content_length", len(description))
            if content_length >= 500:
                score += 0.2
            elif content_length >= 200:
                score += 0.1

        # 设计方法评分
        elif entity_type == "设计方法" or entity_data.get("label") == "DesignMethod":
            # 1. 方法论清晰度 (+0.3)
            if len(description) >= 50:
                score += 0.3

            # 2. 适用场景明确 (+0.2)
            if props.get("applicable_spaces") and len(props.get("applicable_spaces", [])) > 0:
                score += 0.2

            # 3. 有规范支持或案例引用 (+0.3)
            if props.get("seed_source") or props.get("source_standard"):
                score += 0.3

            # 4. 有定量指标 (+0.2)
            # 检查描述中是否包含数值（如"30m²"、"+5Pa"等）
            import re
            if re.search(r'\d+(\.\d+)?[m²㎡Pa%级]', description):
                score += 0.2

        # 一般实体评分
        else:
            # 1. 定量数据 (+0.2)
            quantitative_fields = ['area_m2', 'clear_size', 'floor', 'service_capacity', 'staff_count']
            if any(props.get(field) for field in quantitative_fields):
                score += 0.2

            # 2. 清晰方法论/描述 (+0.3)
            if len(description) >= 30:
                score += 0.3

            # 3. 规范/研究支持 (+0.2)
            if props.get("design_standard_ref") or props.get("regulatory_requirements"):
                score += 0.2

            # 4. 应用场景 (+0.2)
            if props.get("required_adjacencies") or props.get("typical_layout"):
                score += 0.2

            # 5. 视觉资料 (+0.1)
            if props.get("media_refs"):
                score += 0.1

        return min(score, 1.0)

    def _hash_text(self, text: str) -> str:
        """计算文本哈希（SHA256，前16位）"""
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

    def _build_extraction_version(self) -> str:
        """根据 schema 与 LLM 型号生成抽取版本号，用于缓存命中与增量构建。"""
        schema_str = json.dumps(self.schema, ensure_ascii=False, sort_keys=True)
        schema_hash = self._hash_text(schema_str)
        model = self.llm_model
        return f"schema-{schema_hash}_model-{model}"

    def _find_or_create_entity(
        self,
        entity_name: str,
        chunk_id: str,
        entity_type: str | None = None,
        description: str | None = None,
        scope_chain: list[str] | None = None,
    ) -> str:
        """
        在 NetworkX 图里查找或创建一个实体节点，返回稳定 ID。
        - 稳定 ID = hash(标准化名字 + 类型 + 作用域)
        - 已存在则只追加 chunk_id / 补充描述
        """
        # 0) 概念节点：优先引用预注入骨架（全局唯一，避免重复创建）
        if entity_type:
            label = self.concept_to_label.get(entity_type)
            if label and label in self.concept_nodes:
                canonical_name = canonicalize(entity_name, entity_type, self.alias_map)
                concept_node_id = self.concept_nodes[label].get(canonical_name)
                if concept_node_id:
                    # 确保在 NetworkX 图中也有占位节点（后续属性/关系写入都依赖 graph.nodes）
                    if self.graph.has_node(concept_node_id):
                        props = self.graph.nodes[concept_node_id].get("properties", {})
                        chunk_ids = props.get("chunk_ids") or []
                        if chunk_id and chunk_id not in chunk_ids:
                            chunk_ids.append(chunk_id)
                        props["chunk_ids"] = chunk_ids
                        # 以 schema_type 驱动后续写入标签匹配
                        props.setdefault("schema_type", entity_type)
                        if description:
                            existing_desc = props.get("description")
                            if not existing_desc or (description not in existing_desc and len(description) > len(existing_desc)):
                                props["description"] = description
                        props.setdefault("attributes", [])
                        self.graph.nodes[concept_node_id]["properties"] = props
                    else:
                        properties = {
                            "name": entity_name,
                            "chunk_ids": [chunk_id] if chunk_id else [],
                            "attributes": [],
                            "schema_type": entity_type,
                        }
                        if description:
                            properties["description"] = description
                        self.graph.add_node(
                            concept_node_id,
                            label="entity",
                            properties=properties,
                            level=2,
                        )
                        self.node_counter += 1

                    print(f"[Concept] Referencing concept node: {canonical_name} ({label}) -> {concept_node_id}")
                    return concept_node_id

        # 1) 基于名称+类型(+作用域) 生成稳定ID
        entity_node_id = self._generate_stable_entity_id(entity_name, entity_type or "", scope_chain)

        # 2) 已存在：追加 chunk_id / 补充描述 / 补齐类型
        if self.graph.has_node(entity_node_id):
            props = self.graph.nodes[entity_node_id].get("properties", {})
            chunk_ids = props.get("chunk_ids") or []
            if chunk_id and chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
            props["chunk_ids"] = chunk_ids
            if entity_type and not props.get("schema_type"):
                props["schema_type"] = entity_type
            if description:
                existing_desc = props.get("description")
                if not existing_desc:
                    props["description"] = description
                elif description not in existing_desc and len(description) > len(existing_desc):
                    props["description"] = description
            # 回写
            self.graph.nodes[entity_node_id]["properties"] = props
            return entity_node_id

        # 3) 新建节点：最小必需属性
        properties = {
            "name": entity_name,
            "chunk_ids": [chunk_id] if chunk_id else [],
            "attributes": [],            # 轻量属性列表（值）
        }
        if entity_type:
            properties["schema_type"] = entity_type
        if description:
            properties["description"] = description

        self.graph.add_node(
            entity_node_id,
            label="entity",
            properties=properties,
            level=2,   # Layer 2: 实体
        )
        self.node_counter += 1
        return entity_node_id

    def _normalize_source_type(self, category: Optional[str]) -> str:
        cat = (category or "").strip().lower()
        if not cat:
            return "项目文档"
        for canonical, keywords in self.source_type_aliases:
            for kw in keywords:
                if kw and kw.lower() in cat:
                    return canonical
        return "项目文档"

    def _normalize_entity_type_value(self, name: str, raw_type: Any, content: str) -> Tuple[Optional[str], Optional[str]]:
        normalized_type = str(raw_type).strip() if raw_type else ""
        original_type = normalized_type or None
        if normalized_type in self.type_synonyms:
            normalized_type = self.type_synonyms[normalized_type]
        if normalized_type in self.label_to_concept:
            normalized_type = self.label_to_concept[normalized_type]
        if normalized_type == "科室":
            normalized_type = "部门"
        if not normalized_type or (self.allowed_entity_types and normalized_type not in self.allowed_entity_types):
            fallback_type = self._infer_entity_type_soft(name, content) if self.schema_mode_soft else ""
            if not fallback_type and self.entity_type_llm_fallback:
                fallback_type = self._llm_guess_entity_type(name, content)
            normalized_type = fallback_type
        return normalized_type or None, original_type

    def _filter_and_normalize_entities(
        self,
        raw_entity_types: Dict[str, Any],
        content: str,
        chunk_id: str,
    ) -> Dict[str, str]:
        entity_types: Dict[str, str] = {}
        skipped: List[Tuple[str, str]] = []
        for name, raw_type in (raw_entity_types or {}).items():
            normalized, original = self._normalize_entity_type_value(name, raw_type, content)
            if normalized:
                entity_types[name] = normalized
            else:
                if original:
                    skipped.append((name, original))
        if skipped:
            for name, etype in skipped:
                print(f"[INFO] Skip entity '{name}' type='{etype}' not in schema; chunk={chunk_id}")
        return entity_types

    def _ensure_source_node(
        self,
        title: Optional[str],
        category: Optional[str] = None,
        chunk_data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """确保 Source 节点存在，返回 node_id 与元信息。"""
        chunk_data = chunk_data or {}
        normalized_title = (title or "").strip()
        if not normalized_title:
            normalized_title = str(
                chunk_data.get("doc_title")
                or chunk_data.get("source_document")
                or chunk_data.get("doc_id")
                or chunk_data.get("source_directory")
                or "未知资料"
            )

        source_type = self._normalize_source_type(
            category
            or chunk_data.get("doc_source_category")
            or chunk_data.get("source_category")
            or chunk_data.get("doc_type")
            or chunk_data.get("doc_category")
        )
        node_id = self._generate_stable_entity_id(normalized_title, "资料来源")
        chunk_id = str(chunk_data.get("chunk_id") or chunk_data.get("_id") or "")
        doc_id = str(chunk_data.get("doc_id") or "")

        if self.graph.has_node(node_id):
            props = self.graph.nodes[node_id].get("properties", {})
            props.setdefault("chunk_ids", [])
            if chunk_id and chunk_id not in props["chunk_ids"]:
                props["chunk_ids"].append(chunk_id)
            props.setdefault("doc_ids", [])
            if doc_id and doc_id not in props["doc_ids"]:
                props["doc_ids"].append(doc_id)
            if source_type and not props.get("source_type"):
                props["source_type"] = source_type
            if normalized_title and not props.get("title"):
                props["title"] = normalized_title
            self.graph.nodes[node_id]["properties"] = props
        else:
            properties = {
                "title": normalized_title,
                "schema_type": "资料来源",
                "source_type": source_type,
                "chunk_ids": [chunk_id] if chunk_id else [],
                "doc_ids": [doc_id] if doc_id else [],
            }
            self.graph.add_node(
                node_id,
                label="entity",
                properties=properties,
                level=2,
            )
            self.node_counter += 1

        self._source_node_cache[f"{normalized_title.lower()}|{source_type}"] = node_id
        return node_id, {"title": normalized_title, "source_type": source_type}

    def _extract_chunk_page(self, chunk_data: Dict[str, Any]) -> Optional[int]:
        metadata = chunk_data.get("metadata") or {}
        page = metadata.get("page_number") or metadata.get("page") or chunk_data.get("page_number")
        if not page:
            page_range = chunk_data.get("page_range") or []
            if page_range:
                page = page_range[0]
        try:
            return int(page) if page is not None else None
        except Exception:
            return None

    def _extract_media_refs_for_chunk(self, chunk_data: Dict[str, Any]) -> List[str]:
        refs: List[str] = []
        for img in (chunk_data.get("images") or chunk_data.get("image_refs") or []):
            if isinstance(img, dict):
                label = img.get("caption") or img.get("name") or img.get("path") or img.get("url")
            else:
                label = str(img)
            if label:
                refs.append(str(label))
        if chunk_data.get("image_url"):
            refs.append(str(chunk_data["image_url"]))
        return list(dict.fromkeys(refs))[:8]

    def _guess_perspective(self, chunk_data: Dict[str, Any], source_meta: Dict[str, Any]) -> str:
        category = (
            chunk_data.get("doc_category")
            or chunk_data.get("doc_source_category")
            or chunk_data.get("source_category")
            or chunk_data.get("doc_type")
            or source_meta.get("source_type")
            or ""
        ).lower()
        if any(kw in category for kw in ["规范", "标准", "gb", "code"]):
            return "规范要求"
        if any(kw in category for kw in ["案例", "项目", "图集", "参考", "方案"]):
            return "案例数据"
        if any(kw in category for kw in ["论文", "学术", "研究", "journal"]):
            return "学术观点"
        if any(kw in category for kw in ["运营", "管理", "指南", "流程"]):
            return "运营建议"
        if source_meta.get("source_type") == "规范标准":
            return "规范要求"
        if source_meta.get("source_type") == "学术文献":
            return "学术观点"
        if source_meta.get("source_type") == "图集书籍":
            return "案例数据"
        if source_meta.get("source_type") == "会议纪要":
            return "会议纪要"
        return "现状调研"

    @staticmethod
    def _truncate_text(text: str, limit: int = 180) -> str:
        if not text:
            return ""
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "…"

    def _extract_sentence_about_entity(self, content: str, entity_name: str) -> str:
        if not content:
            return ""
        try:
            sentences = re.split(r"(?<=[。！？.!?])", content)
        except Exception:
            sentences = [content]
        for sentence in sentences:
            if entity_name and entity_name in sentence:
                return sentence.strip()
        return self._truncate_text(content, 160)

    def _build_mentioned_in_props(
        self,
        entity_name: str,
        chunk_data: Dict[str, Any],
        entity_descriptions: Dict[str, str],
        source_meta: Dict[str, Any]
    ) -> Dict[str, Any]:
        content = chunk_data.get("content") or ""
        perspective = self._guess_perspective(chunk_data, source_meta)
        description = entity_descriptions.get(entity_name, "")
        summary_candidate = description or self._extract_sentence_about_entity(content, entity_name)
        summary = self._truncate_text(summary_candidate, 200)
        quote = self._extract_sentence_about_entity(content, entity_name)
        page = self._extract_chunk_page(chunk_data)
        media_refs = self._extract_media_refs_for_chunk(chunk_data)

        props: Dict[str, Any] = {"confidence": 0.9}
        if perspective:
            props["perspective"] = perspective
        if summary:
            props["summary"] = summary
        if quote and quote != summary:
            props["quote"] = self._truncate_text(quote, 200)
        if media_refs:
            props["media_refs"] = media_refs
        if page:
            props["page"] = page
        if perspective == "规范要求":
            props["is_compliance"] = True
        return props

    def _link_entity_to_source(
        self,
        entity_node_id: str,
        source_node_id: str,
        chunk_id: str,
        props: Dict[str, Any]
    ) -> None:
        if not entity_node_id or not source_node_id:
            return
        props = dict(props or {})
        confidence = props.pop("confidence", 0.9)
        existing = self.graph.get_edge_data(entity_node_id, source_node_id)
        if existing:
            for key, data in existing.items():
                if data.get("relation") == "MENTIONED_IN" and data.get("chunk_id") == chunk_id:
                    # merge
                    if confidence:
                        data["confidence"] = max(data.get("confidence", 0.0), confidence)
                    for k, v in props.items():
                        if v in (None, "", []):
                            continue
                        if k == "media_refs":
                            merged = list(dict.fromkeys((data.get("media_refs") or []) + list(v)))
                            data["media_refs"] = merged
                        else:
                            data[k] = v
                    return

        edge_attrs = {
            "relation": "MENTIONED_IN",
            "chunk_id": chunk_id,        # 单个chunk_id（向后兼容）
            "chunk_ids": [chunk_id],     # 数组形式（支持多来源）
            "original_relation": "MENTIONED_IN",
            "confidence": confidence,
        }
        for k, v in props.items():
            if v in (None, "", []):
                continue
            edge_attrs[k] = v
        self.graph.add_edge(
            entity_node_id,
            source_node_id,
            **edge_attrs,
        )

    def _link_entities_to_source(
        self,
        entity_node_ids: Dict[str, str],
        source_node_id: Optional[str],
        chunk_id: str,
        chunk_data: Dict[str, Any],
        entity_descriptions: Dict[str, str],
        source_meta: Dict[str, Any],
    ) -> None:
        if not source_node_id:
            return
        for name, node_id in entity_node_ids.items():
            props = self._build_mentioned_in_props(name, chunk_data, entity_descriptions, source_meta)
            self._link_entity_to_source(node_id, source_node_id, chunk_id, props)

    def _ensure_indexes_and_constraints(self) -> None:
        """确保 Mongo 索引与 Neo4j 约束存在（可重复调用）。"""
        # Mongo 索引（忽略已存在的错误）
        try:
            self.chunks_collection.create_index([("chunk_id", 1)], name="chunk_id_idx", unique=True)
        except Exception:
            pass
        try:
            self.chunks_collection.create_index([("doc_id", 1)], name="doc_id_idx")
        except Exception:
            pass
        try:
            self.extractions_collection.create_index([
                ("chunk_id", 1), ("version", 1)
            ], name="chunk_version_idx", unique=True)
        except Exception:
            pass

        # Neo4j 约束:为每个真实标签创建唯一约束 (id)
        try:
            with self.neo4j_driver.session(database=self.neo4j_database) as session:
                for lbl in set(self.type_to_label.values()):
                    session.run(
                        f"""
                        CREATE CONSTRAINT {lbl}_id IF NOT EXISTS
                        FOR (n:`{lbl}`) REQUIRE n.id IS UNIQUE
                        """
                    )
        except Exception:
            pass

    def _get_cached_extraction(self, chunk_id: str) -> Optional[Dict]:
        """按 chunk_id + 版本查询缓存。

        只返回状态为 "success" 的缓存结果，失败的缓存会被忽略（自动重试）。
        """
        try:
            doc = self.extractions_collection.find_one({
                "chunk_id": chunk_id,
                "version": self.extraction_version,
            }, projection={"_id": 0, "result": 1, "status": 1})

            if not doc or "result" not in doc:
                return None

            # 只返回成功的缓存，失败的缓存会被重试
            status = doc.get("status", "success")  # 兼容旧数据，默认为成功
            if status == "success":
                return doc["result"]

            return None
        except Exception:
            return None

    def _cache_extraction_result(self, chunk_id: str, result: Dict, status: str = "success") -> None:
        """写入/更新缓存（幂等）。

        Args:
            chunk_id: chunk ID
            result: 抽取结果
            status: 处理状态 ("success" 或 "failed")
        """
        try:
            self.extractions_collection.update_one(
                {"chunk_id": chunk_id, "version": self.extraction_version},
                {"$set": {
                    "chunk_id": chunk_id,
                    "version": self.extraction_version,
                    "result": result,
                    "status": status,
                    "updated_at": datetime.utcnow(),
                }},
                upsert=True,
            )
        except Exception:
            pass

    @staticmethod
    def _sanitize_rel_type(rel: str) -> str:
        """只保留字母数字与下划线，空则回退 RELATED_TO。"""
        if not rel:
            return "RELATED_TO"
        safe = re.sub(r"[^A-Za-z0-9_]", "_", str(rel))
        return safe or "RELATED_TO"

    @staticmethod
    def _sanitize_label(lbl: str) -> str:
        """仅用于动态标签的安全过滤。"""
        if not lbl:
            return "Entity"
        safe = re.sub(r"[^A-Za-z0-9_]", "_", str(lbl))
        return safe or "Entity"
    
    def load_schema(self, schema_path: str) -> Dict[str, Any]:
        """加载领域schema"""
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema = json.load(f)
                node_defs = schema.get('Labels') or schema.get('NodeConcepts') or []
                label_count = len(node_defs)
                print(f"[OK] Loaded schema with {label_count} label types, "
                      f"{len(schema.get('Relations', []))} relation types")
                return schema
        except FileNotFoundError:
            print(f"[WARN] Schema file not found: {schema_path}")
            return {}
    
    def get_construction_prompt(self, chunk_text: str, content_type: str = "text") -> str:
        """
        生成实体和关系提取的Prompt

        优化点：根据content_type（text/image/table）使用不同的提取策略
        """
        schema_str = json.dumps(self.schema, ensure_ascii=False, indent=2)

        # 根据content_type选择前缀提示
        if content_type == "image":
            type_specific_intro = """你是一个专业的医疗建筑领域知识抽取专家。以下文本是通过VLM从医疗建筑图片（平面图、流程图、示意图、照片等）中提取的描述性内容。

**图片内容特点**：
- 可能包含空间布局、功能分区、流线组织、设备配置等视觉信息
- 图表中的标注、尺寸、说明文字都是重要信息
- 流程图、平面图中的连接关系、相邻关系需要特别关注
- 施工图、案例照片中的设计方法、技术要点需要提取

**提取重点**：
1. **空间实体**: 从图中识别的房间、功能区、设备
2. **空间关系**: CONNECTED_TO（流线连接）、ADJACENT_TO（相邻）、CONTAINS（包含）
3. **设计方法**: 从图例、标注中识别的设计策略（如三区划分、流线分离）
4. **尺寸参数**: 面积、距离、高度等数值信息
"""
        elif content_type == "table":
            type_specific_intro = """你是一个专业的医疗建筑领域知识抽取专家。以下文本是从医疗建筑相关表格中提取的结构化数据。

**表格内容特点**：
- 通常包含规范要求、技术参数、面积指标、设备清单等
- 数据高度结构化，行列对应关系明确
- 可能是设计标准、比较分析、参数列表

**提取重点**：
1. **实体+属性配对**: 表格每行通常对应一个实体及其多个属性
2. **数值信息**: 精确提取数字、单位、范围（如"≥30㎡"、"10-15m"）
3. **规范要求**: 强制性/推荐性指标、适用条件
4. **对比关系**: 表格中的实体比较、参数差异
"""
        else:  # text
            type_specific_intro = """你是一个专业的医疗建筑领域知识抽取专家。请从以下文本中提取实体、关系和属性。

"""

        prompt = f"""{type_specific_intro}
**领域Schema**（参考，但不限于）：
{schema_str}

**提取规则**：
1. **实体类型**：优先且尽量只使用 Schema 的 Labels 类型（医院／部门／功能分区／空间／设计方法／医疗服务／医疗设备／治疗方法／案例／资料来源）
   - **医院**：独立医疗机构（如：综合医院、专科医院、诊所、社区卫生中心）
   - **部门**：医院内的一级组织单元（如：门诊部、急诊部、住院部、医技部）
   - **功能分区**：部门内的功能区块（如：手术部、ICU、急救区、检查治疗区、公共区）
   - **空间**：最小物理单元（如：手术间、病房、诊室、治疗室、门厅、护士站）
   - **设计方法**：设计原则、技术指标、工艺流程（如：洁净手术部三区划分、风压梯度控制、气流组织设计）
   - **医疗服务**：诊疗、护理、检查等服务项目（如：急诊抢救、血液透析、CT检查）
   - **医疗设备**：用于诊疗/监护的设备（如：手术机器人、呼吸机、透析机）
   - **治疗方法**：治疗手段或流程（如：微创手术、介入治疗、康复训练）
   - **资料来源**：规范标准、政策文件、学术论文、书籍报告（如：GB 51039-2014、建筑设计规范、《建筑设计资料集》）
   - ⚠️ 注意区分：急救中心、手术部→功能分区；急诊部→部门；综合医院→医院
   - 🔥 **重要**：PDF文件名（如"XX.pdf"）、带书名号的书籍名（如《XX》）必须识别为"资料来源"，不要误识别为其他类型

2. **关系类型**：优先使用 Schema 中的 Relations（MENTIONED_IN / CONTAINS / CONNECTED_TO / ADJACENT_TO / REQUIRES / GUIDES / PROVIDES / PERFORMED_IN / USES / SUPPORTS / REFERENCES / REFERS_TO）

   **重点A - 空间连接网络**（核心要求）：
   * 当 chunk 中出现 ≥2 个空间或功能分区时，必须判定它们之间的空间关系（CONNECTED_TO / ADJACENT_TO / CONTAINS / REQUIRES）
   * 若文本已明确描述流线或邻接，直接抽取；若根据规范/常识可以确定（如手术间↔刷手间），可谨慎推理
   * 若 chunk 仅涉及 0-1 个空间/分区，可不输出空间关系
   
   **重点B - 业务链路 + 多视角溯源**：
   * 所有非 Source 实体都要连接到出现的资料来源，输出 (Entity)-[MENTIONED_IN {{perspective, summary, quote, page, media_refs}}]->(Source)
   * `perspective` 参考：规范要求 / 案例数据 / 学术观点 / 运营建议 / 现状调研；`summary` 概括资料如何描述该实体，可附一两句 `quote`，若文本提到图片/图纸，将文件名放入 `media_refs`
   * 当 chunk 隐含目录或文件属性（如“某医院案例图集”“GB51039 规范”），请据此补齐 Source 节点并回链
   * 功能分区/空间 -[PROVIDES]-> 医疗服务（如：手术部提供"手术切除"服务）
   * 医疗服务 -[PERFORMED_IN]-> 功能分区/空间（如：血液透析在透析室开展）
   * 功能分区/空间/医疗服务 -[REQUIRES]-> 医疗设备 或 其他空间/分区
   * 治疗方法 -[USES]-> 医疗设备；医疗设备/空间 -[SUPPORTS]-> 治疗方法
   * 资料来源 -[REFERENCES]-> 资料来源（文献互引）

3. **属性**：提取实体的关键属性（如面积、尺寸、标准等）

4. **精确性**：保留原文中的数值、单位、专业术语

5. **完整性与关联性**：
   - 尽可能多地提取有价值的信息；当 chunk 中存在空间/分区/服务/设备/治疗方法等实体时，应构建它们之间的实际业务链路（依赖、提供、使用等）
   - **避免返回RELATED_TO**，如果不确定，请给出最接近的具体关系或跳过

**🔥 重要约束（前端展示优化）**：
1. **实体名称**：必须是清晰、简洁的名词或名词短语（2-6个字），不要提取完整句子
   - ✅ 好的例子："手术室"、"综合医院"、"GB 51039-2014"
   - ❌ 坏的例子："本规范规定的内容"、"综合医院建筑设计的基本要求"
2. **属性提取**：属性应该是精炼的短语（不超过20字），包含关键信息
   - ✅ 好的例子："面积≥30㎡"、"净化等级：百级"
   - ❌ 坏的例子："手术室的面积应该不小于30平方米并且..."
3. **实体去重**：如果实体已经出现过，使用相同的名称
4. **实体描述**：为每个重要实体提供一句话描述（10-30字）

**🧠 专业知识推理**（基于GB 51039-2014等规范标准）：

你是资深医疗建筑设计专家，除了抽取明确写出的信息，还需基于专业知识进行**谨慎推理**。

**推理原则**：
- ✅ 只推理医疗建筑标准中明确要求的配套和依赖（置信度 >= 0.9）
- ✅ 基于功能强相关性推理空间连接
- ❌ 不推理可选的、不确定的、项目特定的关系
- ❌ 宁可少推理，不要错推理

**空间连接推理模板**（参考《综合医院功能分区》标准配置）：

【急诊部推理】
- 急救区：
  * 抢救室 -[REQUIRES]-> 急诊手术室（紧急救治链）
  * 抢救室 -[ADJACENT_TO]-> 急诊监护室（便于转运）
  * 抢救大厅 -[CONNECTED_TO]-> 抢救室（快速通道）
- 急诊区：
  * 诊查室 -[ADJACENT_TO]-> 治疗室（诊疗流程）
  * 清创室 -[ADJACENT_TO]-> 换药室（功能相关）
- 医技区：
  * 挂号室 -[ADJACENT_TO]-> 收费室（服务流程）
  * 药房 -[ADJACENT_TO]-> 候药厅（取药流程）
- 功能分区依赖：
  * 急诊部 -[REQUIRES {{distance_max: 50m}}]-> 影像中心（快速诊断）
  * 急诊部 -[REQUIRES]-> 检验科（快速化验）

【门诊部推理】
- 公共区：
  * 门厅 -[CONNECTED_TO]-> 挂号厅（就诊流程）
  * 挂号厅 -[ADJACENT_TO]-> 预诊/分诊（预约检查）
  * 收费 -[ADJACENT_TO]-> 门诊药房（缴费取药）
- 各科诊区：
  * 诊室 -[ADJACENT_TO]-> 候诊区（候诊-就诊）
  * 外科诊室 -[ADJACENT_TO]-> 治疗室（小手术）
- 检查治疗区：
  * 采血室 -[CONNECTED_TO]-> 检验室（标本传递）
  * 输液室 -[ADJACENT_TO]-> 注射室（治疗配合）
  * 外科换药室 -[ADJACENT_TO]-> 外科创伤处置室（功能连续）

【医技部推理】
- 手术部：
  * 手术间 -[CONNECTED_TO {{door_type: '气密门'}}]-> 刷手间（必备标配）
  * 手术间 -[ADJACENT_TO]-> 器械准备间（标准配置）
  * 手术间 -[ADJACENT_TO]-> 麻醉准备间（标准配置）
  * 手术区 -[REQUIRES]-> 患者准备区（术前准备）
  * 手术部 -[REQUIRES {{distance_max: 30m, is_critical: true}}]-> ICU（术后监护）
  * 手术部 -[REQUIRES]-> 病理科（快速冰冻）
- 消毒供应室（流线重点）：
  * 去污区 -[CONNECTED_TO {{allowed_flows: ['污染'], prohibited_flows: ['洁净']}}]-> 检查包装区（单向流线）
  * 检查包装区 -[CONNECTED_TO {{allowed_flows: ['洁净']}}]-> 无菌存放区（洁净流线）
- 检验科：
  * 普通检验区 -[ADJACENT_TO {{barrier_type: '分隔'}}]-> 微生物检验区（感控分离）
  * 工作区 -[ADJACENT_TO]-> 医护区（工作配合）
- 放射影像中心：
  * 患者走廊 -[CONNECTED_TO]-> 诊断医疗区（患者流线）
  * 医生走廊 -[CONNECTED_TO]-> 医辅区（医护流线，医患分离）
- 功能检查中心：
  * 病人等候区 -[CONNECTED_TO]-> 术前准备区（检查流程）
  * 术前准备区 -[CONNECTED_TO]-> 治疗诊断区（检查流程）
  * 治疗诊断区 -[CONNECTED_TO]-> 术后恢复区（检查流程）
  * 医护工作区 -[ADJACENT_TO]-> 治疗诊断区（医护配合）
- 介入治疗中心：
  * 接待区 -[CONNECTED_TO]-> 准备恢复区（患者流线）
  * 准备恢复区 -[CONNECTED_TO]-> 导管区（治疗流程）
  * 医护工作区 -[ADJACENT_TO]-> 导管区（医护配合）
- 透析室、病理科、输血科：
  * 准备区/工作区 -[CONNECTED_TO]-> 治疗区（流程）
  * 医护区 -[ADJACENT_TO]-> 工作区（配合）
  * 污物处理区 -[CONNECTED_TO {{allowed_flows: ['污染']}}]-> 工作区（单向）

【住院部推理】
- ICU：
  * 监护室 -[ADJACENT_TO]-> 护士站（24h监护，必备）
  * 监护室 -[ADJACENT_TO]-> 污物间（感控）
  * 外科监护室(SICU) -[REQUIRES {{distance_max: 20m}}]-> 手术部（术后转运）
- 护理单元：
  * 病房 -[ADJACENT_TO]-> 护士站（护理便捷）
  * 护士站 -[CONNECTED_TO]-> 治疗室（护理工作）
  * 传染病病房 -[ADJACENT_TO {{barrier_type: '隔离分区'}}]-> 普通病房（感控隔离）

**知识链路推理**（设计决策溯源）：

1. **设计方法 ↔ 规范标准**：
   - 当提到设计方法（如：三区划分、流线设计）时，推理其来源规范
   - 当提到规范条款时，推理对应的设计方法

2. **资料引用网络**：
   - 当提到新规范时，推理它可能引用的旧规范（如：2014版→2008版）
   - 当提到论文时，推理它的参考文献关系
   - 当提到标准时，推理它可能引用的国际标准

3. **🔥 跨章节引用识别**（新增）：
   - **显式引用标记**：识别文本中的跨引用标记，如：
     * "参见第X章/节" → (当前Source)-[REFERENCES {{target_section: "第X章"}}]->(同一Source)
     * "如第Y节所述" → (当前实体)-[REFERS_TO {{source_section: "第Y节"}}]->(目标实体)
     * "详见附录A" → (当前Source)-[REFERENCES {{target_section: "附录A"}}]->(同一Source)
     * "参考图X-Y" → 将图片文件名记录到 `media_refs` 字段
   - **图表引用**：当提到"见图X-Y"、"表X-Y所示"时，提取图表编号放入 `media_refs`
   - **条款引用**：当提到"依据X.X.X条"时，创建条款节点并建立GUIDES关系
   - **输出格式**：
     * 对于同文档跨章节: ["实体A", "REFERS_TO", "实体B"]，在关系属性中添加 source_section 和 target_section
     * 对于跨文档引用: ["Source A", "REFERENCES", "Source B"]
   - **重要约束**：只提取明确的引用标记，不推理隐式引用

**推理质量控制**：
- 仅当推理置信度 >= 0.9 时才输出该关系
- 推理的关系与明确的关系输出格式完全一致（无需标注）
- 当不确定时，不要强行推理

**输出格式**（JSON）：
{{
  "entities": {{
    "实体名": {{
      "type": "实体类型",
      "description": "一句话描述这个实体（10-30字）"
    }}
  }},
  "attributes": {{
    "实体名": ["属性1", "属性2"]
  }},
  "triples": [
    ["主体", "关系", "客体"]
  ],
  "new_schema_types": {{
    "nodes": ["新发现的实体类型"],
    "relations": ["新发现的关系类型"],
    "attributes": ["新发现的属性类型"]
  }}
}}

**待处理文本**：
{chunk_text}

请返回JSON格式的提取结果："""

        return prompt
    
    def extract_with_llm(self, prompt: str) -> Optional[Dict]:
        """
        调用LLM提取实体和关系
        """
        try:
            result = self.llm_client.chat_json(
                messages=[{"role": "user", "content": prompt}], temperature=0.1
            )
            return result or None
        except Exception as e:
            print(f"[ERROR] LLM extraction failed: {e}")
            return None
    
    def _extract_media_refs(self, chunk_content: str, chunk_data: Dict = None) -> Dict[str, Any]:
        """
        从chunk中提取富媒体引用信息
        
        Args:
            chunk_content: chunk文本内容
            chunk_data: 完整的chunk数据（包含images, tables字段）
            
        Returns:
            富媒体引用字典
        """
        media_refs = {
            "has_image": False,
            "has_table": False,
            "image_refs": [],
            "table_refs": []
        }
        
        # 从chunk数据中提取（如果提供）
        if chunk_data:
            images = chunk_data.get("images", [])
            tables = chunk_data.get("tables", [])
            
            if images:
                media_refs["has_image"] = True
                media_refs["image_refs"] = [
                    {
                        "id": img.get("id"),
                        "caption": img.get("caption", ""),
                        "path": img.get("path", "")
                    }
                    for img in images
                ]
            
            if tables:
                media_refs["has_table"] = True
                media_refs["table_refs"] = [
                    {
                        "id": tbl.get("id"),
                        "caption": tbl.get("caption", "")
                    }
                    for tbl in tables
                ]
        
        # 从文本中推断（备用方案）
        if not media_refs["has_image"]:
            # 检测图片引用
            image_patterns = [r'图\s*\d+[-\.\d]*', r'Fig\.*\s*\d+', r'图示', r'如图所示']
            for pattern in image_patterns:
                if re.search(pattern, chunk_content):
                    media_refs["has_image"] = True
                    break
        
        if not media_refs["has_table"]:
            # 检测表格引用
            table_patterns = [r'表\s*\d+[-\.\d]*', r'Table\s*\d+', r'见表']
            for pattern in table_patterns:
                if re.search(pattern, chunk_content):
                    media_refs["has_table"] = True
                    break
        
        return media_refs

    def _infer_entity_type_soft(self, name: str, context: str) -> str:
        """启发式类型归类（软模式）
        规则：
        - 命名后缀/关键词匹配："手术间/室/刷手间/走廊/卫生间/办公室/候诊区" → 空间
        - 区/中心/科/部 → 功能分区（部/中心）或 部门（部），优先按 schema 中存在的概念
        - 方法/原则/规范/策略 → 设计方法
        - 案例/项目/工程 → 案例
        - 资料/规范/标准/文件/文献 → 资料来源
        默认：空间
        """
        s = (name or "").strip().lower()
        if not s:
            return "空间"

        # [FIX] 优先判断：如果是PDF文件名或包含文件扩展名，直接识别为资料来源
        if s.endswith('.pdf') or s.endswith('.doc') or s.endswith('.docx') or '.pdf' in s:
            return "资料来源"
        # 如果包含《》书名号，很可能是资料来源
        if '《' in name and '》' in name:
            return "资料来源"

        space_markers = ["间", "室", "走廊", "卫生间", "候诊", "护士站", "办公室", "诊室", "复苏室", "库房", "机房", "缓冲", "刷手", "配餐"]
        if any(m in s for m in space_markers):
            return "空间"
        if any(x in s for x in ["方法", "原则", "策略", "工艺", "流程"]):
            return "设计方法"
        if any(x in s for x in ["案例", "项目", "工程"]):
            return "案例"
        if any(x in s for x in ["规范", "标准", "文件", "文献", "指南", "图集", "手册", "资料集"]):
            return "资料来源"
        if any(x in s for x in ["部", "中心", "科", "区"]):
            # 粗略地按功能分区
            return "功能分区"
        return "空间"

    def _llm_guess_entity_type(self, name: str, context: str) -> str:
        try:
            prompt = (
                "只回答以下之一：医院/部门/功能分区/空间/设计方法/案例/资料来源；\n"
                f"名称：{name}\n"
                f"上下文：{context[:400]}\n"
            )
            resp = self.llm_client.chat_json(messages=[{"role": "user", "content": prompt}], temperature=0)
            if isinstance(resp, dict):
                t = (resp.get("type") or resp.get("label") or "").strip()
            else:
                t = str(resp).strip()
            return t if t in self.allowed_entity_types or t in {"医院","部门","功能分区","空间","设计方法","案例","资料来源"} else ""
        except Exception:
            return ""
    
    def _llm_verify_relation(self, subj: str, subj_type: str, relation: str, obj: str, obj_type: str, context: str = "") -> bool:
        """
        使用LLM判断关系是否在医疗建筑领域合理
        返回: True表示合理，False表示不合理
        """
        try:
            # 关系的中文含义
            relation_chinese_map = {
                "MENTIONED_IN": "提及于",
                "CONTAINS": "包含",
                "CONNECTED_TO": "连接",
                "ADJACENT_TO": "毗邻",
                "REQUIRES": "依赖",
                "GUIDES": "指导",
                "REFERENCES": "引用",
                "PROVIDES": "提供",
                "PERFORMED_IN": "开展于",
                "USES": "使用",
                "SUPPORTS": "支持",
            }
            relation_cn = relation_chinese_map.get(relation, relation)
            
            prompt = f"""你是医疗建筑领域专家。请判断以下关系在医疗建筑设计领域是否合理。

关系: {subj}({subj_type}) -[{relation_cn}]-> {obj}({obj_type})

上下文: {context[:300] if context else "无"}

请只回答JSON格式: {{"reasonable": true/false, "reason": "简短理由"}}

⚠️ 重要提示：
1. **优先根据实体名称的语义判断**，而不是只看类型标签
2. **类型标签可能有误**，需要结合实体名称本身的含义

判断标准：
- 医院实体可以包含部门（如：综合医院 → 门诊部、急诊部）
- 医院实体可以包含功能分区（如：综合医院 → 急救中心、手术部、ICU）
- 部门可以包含功能分区（如：急诊部 → 急救区、观察区）
- 功能分区可以包含空间（如：手术部 → 手术间；ICU → 病房）
- 设计方法可以指导空间/功能分区/部门的设计
- 任意实体都可以被资料来源提及 (MENTIONED_IN)，该关系主要用于溯源而非包含

特殊说明：
- "急救中心"、"手术部"、"ICU"等虽然可能被标为"医院"，但语义上是功能分区，可以被医院包含 ✓
- "门诊部"、"急诊部"虽然可能被标为"医院"，但语义上是部门，可以被医院包含 ✓
- "综合医院"、"专科医院"、"诊所"如果被包含，需判断是否真的是独立机构还是部门/分区
- 同类型且同层级的实体（如：医院→医院、部门→部门）一般不应有包含关系 ✗"""

            resp = self.llm_client.chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            
            if isinstance(resp, dict):
                is_reasonable = resp.get("reasonable", False)
                reason = resp.get("reason", "")
                if is_reasonable:
                    print(f"[LLM-VERIFY] ✓ Approved: {subj}({subj_type}) -[{relation}]-> {obj}({obj_type}) | {reason}")
                else:
                    print(f"[LLM-VERIFY] ✗ Rejected: {subj}({subj_type}) -[{relation}]-> {obj}({obj_type}) | {reason}")
                return bool(is_reasonable)
            return False
        except Exception as e:
            print(f"[WARN] LLM relation verification failed: {e}")
            return False
    
    def _add_attributes_to_graph(
        self, 
        attributes: Dict, 
        chunk_id: str, 
        chunk_content: str,
        entity_types: Dict, 
        entity_descriptions: Dict = None,
        chunk_data: Dict = None,
        source_document: str = ""
    ):
        """
        属性处理：将属性存储为实体properties + Milvus向量
        
        Args:
            attributes: 属性字典 {实体名: [属性列表]}
            chunk_id: 来源chunk ID
            chunk_content: chunk完整内容（用于向量化）
            entity_types: 实体类型
            entity_descriptions: 实体描述
            chunk_data: 完整chunk数据（包含富媒体）
            source_document: 来源文档
        """
        if entity_descriptions is None:
            entity_descriptions = {}
        
        for entity, attrs in attributes.items():
            # 查找或创建实体节点
            entity_node_id = self._find_or_create_entity(
                entity, chunk_id, entity_types.get(entity), entity_descriptions.get(entity)
            )
            
            # 获取实体节点
            entity_node = self.graph.nodes[entity_node_id]
            
            # 初始化属性列表（如果不存在）
            if "attributes" not in entity_node["properties"]:
                entity_node["properties"]["attributes"] = []

            # 处理每个属性 - 只存储在Neo4j的properties中
            for attr in attrs:
                attr_type = classify_attribute_type(attr)
                entity_node["properties"]["attributes"].append(attr)

    def _generate_stable_entity_id(self, entity_name: str, entity_type: str = "", scope_chain: Optional[List[str]] = None) -> str:
        """
        生成基于名称+类型的稳定ID
        
        使用SHA256哈希确保：
        1. 同名同类型的实体总是生成相同ID
        2. 跨批次运行不会冲突
        3. ID唯一性
        
        Args:
            entity_name: 实体名称
            entity_type: 实体类型
            
        Returns:
            稳定的实体ID，格式: entity_{hash}
        """
        # 创建唯一键：类型:标准名|作用域（应用别名映射）
        canonical = canonicalize(entity_name, entity_type, self.alias_map)
        scope = compose_scope_key(scope_chain or [])
        unique_key = f"{entity_type}:{canonical}|{scope}".lower().strip()
        
        # 生成SHA256哈希，取前16位（足够避免冲突）
        hash_value = hashlib.sha256(unique_key.encode('utf-8')).hexdigest()[:16]
        
        return f"entity_{hash_value}"

    def process_chunk(self, chunk: Dict[str, Any], source_document: str = "") -> bool:
        """
        处理单个chunk

        特性：
        1. 使用属性内嵌存储
        2. 提取富媒体引用
        3. 传递完整chunk数据给Milvus
        4. 根据content_type使用针对性prompt
        """
        try:
            chunk_id = chunk.get("chunk_id") or chunk.get("_id")
            content = chunk.get("content", "")
            content_type = chunk.get("content_type", "text")  # 新增：获取chunk类型

            if not content or len(content) < 20:
                return False

            # 缓存键回退：若无 chunk_id，用内容哈希
            if not chunk_id:
                chunk_id = f"hash_{self._hash_text(content)}"

            # 命中缓存则跳过 LLM
            extracted = self._get_cached_extraction(str(chunk_id))
            cached = extracted is not None  # 标记是否从缓存读取

            if not extracted:
                # 新增：根据content_type生成针对性prompt
                prompt = self.get_construction_prompt(content, content_type)
                extracted = self.extract_with_llm(prompt)
                if not extracted:
                    # 失败的不缓存，下次会自动重试
                    return False

            # 提取数据（适配新格式）
            entities = extracted.get("entities", {})
            attributes = extracted.get("attributes", {})
            triples = extracted.get("triples", [])

            raw_entity_types = {
                name: info.get("type") if isinstance(info, dict) else info
                for name, info in entities.items()
            }
            entity_descriptions = {
                name: info.get("description", "")
                for name, info in entities.items()
                if isinstance(info, dict)
            }

            entity_types = self._filter_and_normalize_entities(raw_entity_types, content, str(chunk_id))

            entity_descriptions = {
                name: entity_descriptions.get(name, "") for name in entity_types
            }

            # ✅ 关键修复：验证失败时的处理
            if not entity_types:
                # 如果是从缓存读取的，删除无效缓存
                if cached:
                    try:
                        self.extractions_collection.delete_one({
                            "chunk_id": str(chunk_id),
                            "version": self.extraction_version
                        })
                    except Exception:
                        pass
                return False

            attributes = {
                name: values
                for name, values in (attributes or {}).items()
                if name in entity_types
            }

            filtered_triples = []
            for triple in triples or []:
                if not isinstance(triple, (list, tuple)) or len(triple) != 3:
                    continue
                subj, pred, obj = triple
                if subj not in entity_types or obj not in entity_types:
                    continue
                filtered_triples.append(triple)
            triples = filtered_triples

            if not triples and not attributes:
                # 如果是从缓存读取的，删除无效缓存
                if cached:
                    try:
                        self.extractions_collection.delete_one({
                            "chunk_id": str(chunk_id),
                            "version": self.extraction_version
                        })
                    except Exception:
                        pass
                return False

            # ✅ 只有验证通过后才缓存（非缓存数据）
            if not cached:
                self._cache_extraction_result(str(chunk_id), extracted, status="success")

            source_label = (
                source_document
                or chunk.get("doc_title")
                or chunk.get("source_document")
                or chunk.get("file_name")
                or chunk.get("source_directory")
                or chunk.get("source_category")
            )
            if not source_label:
                doc_id = chunk.get("doc_id")
                source_label = str(doc_id) if doc_id else ""
            source_category = (
                chunk.get("doc_category")
                or chunk.get("doc_source_category")
                or chunk.get("source_category")
                or chunk.get("doc_type")
            )
            
            # 构建图
            with self.lock:
                entity_node_ids: Dict[str, str] = {}
                for name in entity_types.keys():
                    scope_chain = self._infer_scope_chain(name, entity_types)
                    node_id = self._find_or_create_entity(
                        name,
                        str(chunk_id),
                        entity_types.get(name),
                        entity_descriptions.get(name),
                        scope_chain=scope_chain,
                    )
                    entity_node_ids[name] = node_id

                source_node_id = None
                source_meta: Dict[str, Any] = {}
                if source_label:
                    source_node_id, source_meta = self._ensure_source_node(
                        source_label,
                        source_category,
                        chunk_data=chunk,
                    )

                # 属性处理
                self._add_attributes_to_graph(
                    attributes, str(chunk_id), content, entity_types, entity_descriptions, 
                    chunk_data=chunk, source_document=source_label
                )
                # 关系处理
                self._add_triples_to_graph(triples, str(chunk_id), entity_types, entity_descriptions, content=content)

                if source_node_id:
                    self._link_entities_to_source(
                        entity_node_ids,
                        source_node_id,
                        str(chunk_id),
                        chunk,
                        entity_descriptions,
                        source_meta,
                    )

                # 注册chunk实体，用于后续共现增强
                self._register_chunk_entities(str(chunk_id), entity_types, chunk)
                # 图片回链（可选）：默认关闭，避免大量图片进入图；由检索层通过 Mongo 返回
                if self.link_images:
                    try:
                        space_node_ids = []
                        for name, etype in (entity_types or {}).items():
                            if (isinstance(etype, str) and etype == "空间") or (isinstance(etype, dict) and etype.get("type") == "空间"):
                                sid = self._find_or_create_entity(name, str(chunk_id), "空间", entity_descriptions.get(name, ""))
                                space_node_ids.append(sid)
                        images = chunk.get("images", []) or chunk.get("image_refs", []) or []
                        if not images:
                            # 回退：从 markdown/text 中解析 ![]() 或 <img src="...">
                            md_text = chunk.get("markdown") or content
                            images = self._extract_images_from_markdown_text(md_text)
                        if space_node_ids and images:
                            self._link_space_images(space_node_ids, images, str(chunk_id), source_label)
                    except Exception:
                        pass
            
            return True
            
        except Exception as e:
            print(f"[ERROR] Processing chunk failed: {e}")
            return False
    
    def _add_triples_to_graph(self, triples: List, chunk_id: str, entity_types: Dict, entity_descriptions: Dict = None, content: str = ""):
        """添加三元组到图（关系处理）"""
        if entity_descriptions is None:
            entity_descriptions = {}
        
        for triple in triples:
            if len(triple) != 3:
                continue
            
            subj, pred, obj = triple
            
            # 查找或创建主体和客体节点
            subj_node_id = self._find_or_create_entity(
                subj, chunk_id, entity_types.get(subj), entity_descriptions.get(subj),
                scope_chain=self._infer_scope_chain(subj, entity_types)
            )
            obj_node_id = self._find_or_create_entity(
                obj, chunk_id, entity_types.get(obj), entity_descriptions.get(obj),
                scope_chain=self._infer_scope_chain(obj, entity_types)
            )
            
            relation_normalized = normalize_relation(pred)
            # 跳过SKIP（不应作为关系的词，如"具有"、"描述"等）
            if relation_normalized == "SKIP":
                continue

            confidence = 0.85
            extra_props: Dict[str, Any] = {}
            refined_relation = False
            if relation_normalized in {"RELATED_TO", "UNKNOWN", ""}:
                relation_normalized, confidence, extra_props = self._refine_relation(
                    subj,
                    entity_types.get(subj),
                    obj,
                    entity_types.get(obj),
                    pred,
                    content
                )
                refined_relation = True

            if self.drop_uncertain_relations and relation_normalized == "CO_OCCUR":
                continue

            if (
                self.allowed_relation_types
                and relation_normalized not in self.allowed_relation_types
                and relation_normalized not in {"BELONGS_TO", "REQUIRED_BY"}
            ):
                print(
                    f"[INFO] Skip relation '{pred}' -> '{relation_normalized}' (chunk={chunk_id})"
                )
                continue

            # 端点类型约束校验（严格对位）
            try:
                subj_type = entity_types.get(subj)
                obj_type = entity_types.get(obj)
                if subj_type in self.type_synonyms:
                    subj_type = self.type_synonyms[subj_type]
                if obj_type in self.type_synonyms:
                    obj_type = self.type_synonyms[obj_type]
                allowed = self.relation_constraints.get(relation_normalized)
                if allowed:
                    subjects_allowed, objects_allowed = allowed
                    mismatch = (subj_type not in subjects_allowed) or (obj_type not in objects_allowed)
                    if mismatch:
                        allow_mismatch = os.getenv("KG_RELATION_ALLOW_MISMATCH", "0").lower() in {"1", "true", "yes"}
                        if self.relation_llm_fallback:
                            is_reasonable = self._llm_verify_relation(
                                subj, subj_type, relation_normalized, obj, obj_type, content
                            )
                            if not is_reasonable and not allow_mismatch:
                                continue
                            if not is_reasonable and allow_mismatch:
                                print(
                                    f"[WARN] Allowing mismatched endpoints (LLM unlikely): {subj}({subj_type}) -[{relation_normalized}]-> {obj}({obj_type}); chunk={chunk_id}"
                                )
                        else:
                            if not allow_mismatch:
                                print(
                                    f"[INFO] Skip relation by endpoint types: {subj}({subj_type}) -[{relation_normalized}]-> {obj}({obj_type}); chunk={chunk_id}"
                                )
                                continue
                            else:
                                print(
                                    f"[WARN] Allowing mismatched endpoints: {subj}({subj_type}) -[{relation_normalized}]-> {obj}({obj_type}); chunk={chunk_id}"
                                )
            except Exception:
                pass

            edge_attrs = {
                "relation": relation_normalized,
                "chunk_id": chunk_id,        # 单个chunk_id（向后兼容）
                "chunk_ids": [chunk_id],     # 数组形式（支持多来源）
                "original_relation": pred,
                "confidence": confidence,
            }
            if refined_relation and extra_props:
                edge_attrs.update(extra_props)

            self.graph.add_edge(
                subj_node_id,
                obj_node_id,
                **edge_attrs
            )

            # 自动生成反向关系
            inverse_relation = get_inverse_relation(relation_normalized)
            if (
                inverse_relation
                and (
                    inverse_relation in self.allowed_relation_types
                    or inverse_relation in {"BELONGS_TO", "REQUIRED_BY"}
                )
            ):
                inverse_attrs = {
                    "relation": inverse_relation,
                    "chunk_id": chunk_id,        # 单个chunk_id（向后兼容）
                    "chunk_ids": [chunk_id],     # 数组形式（支持多来源）
                    "original_relation": f"inverse_of_{pred}",
                    "confidence": confidence,
                    "is_inverse": True,
                }
                if refined_relation and extra_props:
                    inverse_attrs.update(extra_props)

                self.graph.add_edge(
                    obj_node_id,
                    subj_node_id,
                    **inverse_attrs
                )

    def _find_or_create_image_asset(self, image: Dict[str, Any], chunk_id: str, source_document: str = "") -> str:
        """在图中创建/查找图片资源节点（作为通用实体，schema_type=ImageAsset）。"""
        key_source = image.get("path") or image.get("url") or image.get("id") or json.dumps(image, ensure_ascii=False)
        uid = self._hash_text(str(key_source))
        node_id = f"image_{uid}"
        name = image.get("caption") or image.get("name") or os.path.basename(str(image.get("path", "") or str(image.get("url", ""))))
        properties = {
            "name": name or node_id,
            "schema_type": "ImageAsset",
            "chunk_ids": [chunk_id],
            "image_url": image.get("url") or image.get("path"),
            "image_caption": image.get("caption", ""),
            "source_document": source_document,
        }
        if self.graph.has_node(node_id):
            if chunk_id not in self.graph.nodes[node_id]["properties"]["chunk_ids"]:
                self.graph.nodes[node_id]["properties"]["chunk_ids"].append(chunk_id)
        else:
            self.graph.add_node(node_id, label="entity", properties=properties, level=1)
            self.node_counter += 1
        return node_id

    def _link_space_images(self, space_node_ids: List[str], images: List[Dict[str, Any]], chunk_id: str, source_document: str = "") -> None:
        if not images or not space_node_ids:
            return
        for img in images:
            img_id = self._find_or_create_image_asset(img, chunk_id, source_document)
            for sid in space_node_ids:
                self.graph.add_edge(sid, img_id, relation="ILLUSTRATED_BY", chunk_id=chunk_id, original_relation="ILLUSTRATED_BY", confidence=0.9)

    @staticmethod
    def _extract_images_from_markdown_text(md_text: str) -> List[Dict[str, Any]]:
        """从 Markdown/HTML 片段中解析图片引用，返回 [{path/url, caption}]。
        优先提取相对路径（images/xxx.jpg），caption 使用 alt 文本。
        """
        if not md_text or not isinstance(md_text, str):
            return []
        out: List[Dict[str, Any]] = []
        try:
            # Markdown: ![alt](url)
            md_pat = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
            for alt, url in md_pat.findall(md_text):
                out.append({"path": url, "caption": alt.strip()})
            # HTML: <img src="..." alt="...">
            html_pat = re.compile(r"<img[^>]*src=\"([^\"]+)\"[^>]*alt=\"([^\"]*)\"[^>]*>")
            for url, alt in html_pat.findall(md_text):
                out.append({"path": url, "caption": alt.strip()})
            # 简化的 HTML（无 alt）
            html_pat2 = re.compile(r"<img[^>]*src=\"([^\"]+)\"[^>]*>")
            for url in html_pat2.findall(md_text):
                out.append({"path": url, "caption": ""})
        except Exception:
            return out
        return out
    
    def _infer_scope_chain(self, entity_name: str, entity_types: Dict[str, Any]) -> List[str]:
        """根据已知实体类型推断作用域：
        - 空间 → 功能单元/科室/部门
        - 功能单元 → 科室/部门
        - 科室 → 部门
        这里采用保守策略：仅返回上位类型名称的集合（若已在当前 chunk 的实体集中出现）。
        """
        type_name = entity_types.get(entity_name)
        scope: List[str] = []
        # 提取同一chunk内的上位实体名称集
        if type_name == "空间":
            scope.extend([n for n, t in entity_types.items() if (t in {"功能分区", "部门", "科室", "功能单元"})])
        elif type_name in {"功能分区", "功能单元"}:
            scope.extend([n for n, t in entity_types.items() if t in {"部门", "科室"}])
        elif type_name == "科室":
            scope.extend([n for n, t in entity_types.items() if t == "部门"]) 
        return scope

    def build_from_mongodb(
        self,
        limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, int, int], None]] = None,
    ) -> Dict[str, int]:
        """
        从MongoDB中读取chunks并构建知识图谱

        Args:
            limit: 限制处理的文档数量（None表示全部）

        Returns:
            统计信息
        """
        print(f"\n{'='*60}")
        print("Starting Knowledge Graph Construction from MongoDB")
        print(f"{'='*60}\n")

        # 新增：检查是否启用断点续传
        skip_processed = os.getenv("KG_SKIP_PROCESSED", "1").lower() in {"1", "true", "yes"}
        if skip_processed:
            print("[INFO] Incremental mode enabled - will skip already processed chunks")

        if self.enable_cooccurrence_aug:
            self.chunk_doc_map.clear()
            self.chunk_order_map.clear()
            self._chunk_entity_index.clear()
            self._chunk_sequence_counter = 0

        # 使用基于 _id 的分页读取，避免长时间持有游标导致 CursorNotFound
        # 批大小可通过环境变量 KG_MONGO_READ_BATCH 配置，默认 1000
        batch_size = int(os.getenv("KG_MONGO_READ_BATCH", "1000"))
        projection = {
            # 注意：不要排除 _id，让其随结果返回用于分页
            "chunk_id": 1,
            "content": 1,
            "doc_id": 1,
            "images": 1,
            "tables": 1,
            "page_number": 1,
        }
        total_chunks = 0
        success_chunks = 0
        failed_chunks = 0

        # 预加载文档ID到标题的映射（避免重复查询）
        doc_id_to_title = {}
        try:
            docs = self.documents_collection.find({}, {"_id": 1, "title": 1})
            for doc in docs:
                # 确保 _id 转换为字符串（处理 ObjectId）
                doc_id = str(doc["_id"])
                title = doc.get("title") or f"文档_{doc_id[:8]}"
                doc_id_to_title[doc_id] = title
            print(f"[INFO] 加载了 {len(doc_id_to_title)} 个文档的标题映射")
            # 调试：打印前几个映射示例
            if doc_id_to_title:
                sample_items = list(doc_id_to_title.items())[:3]
                for did, ttl in sample_items:
                    print(f"[DEBUG] 映射示例: {did} -> {ttl}")
        except Exception as e:
            print(f"[WARN] 无法加载文档标题映射: {e}，将使用 doc_id 作为 source_document")

        last_id = None
        processed_doc_ids = set()  # 跟踪已处理的文档ID
        mapping_failures = []  # 跟踪映射失败的 doc_id
        skipped_chunks = 0  # 跟踪跳过的 chunks

        def _notify_progress():
            if progress_callback:
                try:
                    processed = success_chunks + skipped_chunks + failed_chunks
                    progress_callback(processed, success_chunks, skipped_chunks, failed_chunks)
                except Exception:
                    pass

        # 新增：chunk类型统计
        chunk_type_stats = defaultdict(int)

        with self.mongo_client.start_session(causal_consistency=False) as session:
            while True:
                # 新增：筛选chunk类型（text, image, table）
                query = {
                    "content_type": {"$in": ["text", "image", "table"]}
                }
                if last_id is not None:
                    query["_id"] = {"$gt": last_id}

                # 如果设置了 limit，则每次批量不超过剩余数量
                current_limit = None
                if limit:
                    remaining = max(limit - total_chunks, 0)
                    if remaining == 0:
                        break
                    current_limit = min(batch_size, remaining)
                else:
                    current_limit = batch_size

                cursor = (
                    self.chunks_collection
                    .find(query, projection=projection, sort=[("_id", 1)], limit=current_limit, session=session)
                )

                batch = list(cursor)
                if not batch:
                    break

                for chunk in batch:
                    total_chunks += 1

                    # 新增：检查是否已经处理过（断点续传）
                    chunk_id = chunk.get("chunk_id") or chunk.get("_id")
                    if skip_processed and chunk_id:
                        # 检查缓存中是否已有该 chunk 的抽取结果
                        cached = self._get_cached_extraction(str(chunk_id))
                        if cached:
                            skipped_chunks += 1
                            if skipped_chunks % 500 == 0:
                                print(f"   [SKIP] Skipped {skipped_chunks} already processed chunks")
                            # 记录 doc_id（即使跳过也要统计）
                            doc_id_raw = chunk.get("doc_id")
                            if doc_id_raw:
                                processed_doc_ids.add(str(doc_id_raw))
                            _notify_progress()
                            continue

                    # 获取 doc_id 并映射到文档标题
                    # 确保 doc_id 转换为字符串（处理 ObjectId）
                    doc_id_raw = chunk.get("doc_id")
                    if doc_id_raw is None:
                        doc_id = ""
                        src = ""
                    else:
                        doc_id = str(doc_id_raw)
                        if doc_id:
                            processed_doc_ids.add(doc_id)
                            # 优先使用文档标题，如果没有则使用 doc_id
                            src = doc_id_to_title.get(doc_id)
                            if src is None:
                                # 映射失败，记录并回退到 doc_id
                                if doc_id not in mapping_failures:
                                    mapping_failures.append(doc_id)
                                src = doc_id
                        else:
                            src = ""

                    # 新增：统计chunk类型
                    content_type = chunk.get("content_type", "unknown")
                    chunk_type_stats[content_type] += 1

                    if self.process_chunk(chunk, source_document=src):
                        success_chunks += 1
                    else:
                        failed_chunks += 1
                    _notify_progress()
                    if (total_chunks - skipped_chunks) % 200 == 0:
                        print(f"   Processed {total_chunks - skipped_chunks} chunks ({success_chunks} successful, {skipped_chunks} skipped)")

                # 记录本批次最后一个 _id，用于下一页
                last_id = batch[-1]["_id"]

        # 新增：输出chunk类型统计
        print(f"\n[INFO] Chunk类型分布:")
        for content_type, count in sorted(chunk_type_stats.items()):
            print(f"  - {content_type}: {count:,}")

        # 输出处理的文档统计
        print(f"\n[INFO] 处理的文档数量: {len(processed_doc_ids)}")
        if len(processed_doc_ids) > 0:
            print(f"[INFO] 文档列表:")
            for doc_id in sorted(processed_doc_ids):
                title = doc_id_to_title.get(doc_id)
                if title:
                    print(f"  - {doc_id}: {title}")
                else:
                    print(f"  - {doc_id}: [映射失败，使用 doc_id]")
        
        # 输出映射失败统计
        if mapping_failures:
            print(f"\n[WARN] 有 {len(mapping_failures)} 个 doc_id 映射失败:")
            for failed_id in mapping_failures[:10]:  # 只显示前10个
                print(f"  - {failed_id}")
            if len(mapping_failures) > 10:
                print(f"  ... 还有 {len(mapping_failures) - 10} 个")
            print(f"[WARN] 这些 doc_id 将使用自身作为 source_document")

        _notify_progress()

        stats = {
            "total_documents": len(processed_doc_ids),
            "total_chunks": total_chunks,
            "success_chunks": success_chunks,
            "failed_chunks": failed_chunks,
            "skipped_chunks": skipped_chunks,
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges()
        }

        print(f"\n{'='*60}")
        print("Construction Statistics:")
        print(f"  Documents: {stats['total_documents']}")
        print(f"  Chunks: {stats['total_chunks']} (Success: {stats['success_chunks']}, Skipped: {stats['skipped_chunks']})")
        print(f"  Nodes: {stats['total_nodes']}")
        print(f"  Edges: {stats['total_edges']}")
        print(f"{'='*60}\n")

        if self.enable_cooccurrence_aug:
            self._chunk_sequence_counter = 0

        return stats
    
    def write_to_databases(self) -> bool:
        """
        写入数据库（Neo4j + MongoDB 溯源）

        流程：
        1) （可选）共现/规则补边增强
        2) 写入 Neo4j
        3) 输出写入摘要
        
        Returns:
            成功返回True，失败返回False
        """
        print(f"\n{'='*60}")
        print("Writing Knowledge Graph to Neo4j")
        print(f"{'='*60}\n")

        self.last_write_summary = {}

        print("[Phase] Writing to Neo4j...")
        try:
            if self.enable_cooccurrence_aug:
                print("[INFO] Augmenting relations via co-occurrence...")
                win_env = os.getenv("KG_COOCCUR_WINDOW")
                sup_env = os.getenv("KG_COOCCUR_MIN_SUPPORT")
                if win_env is None and sup_env is None:
                    print("[INFO] Co-occurrence augmentation skipped (no KG_COOCCUR_* configuration).")
                else:
                    try:
                        win = int(win_env) if win_env is not None else 3
                    except Exception:
                        win = 3
                    try:
                        sup = int(sup_env) if sup_env is not None else 3
                    except Exception:
                        sup = 3
                    self.augment_relations_by_cooccurrence(window=win, min_support=sup)

            # 基于领域规则的补边（可通过环境变量关闭）
            if os.getenv("KG_RULES_AUG", "1").lower() in {"1", "true", "yes"}:
                print("[INFO] Augmenting relations via domain rules...")
                self.augment_relations_by_rules()
            self.write_to_neo4j()
            # 全局拓扑增强已移除（避免长时间阻塞构建）；如需启用，请改为离线脚本
            print(f"[OK] Graph written to Neo4j")
            
        except Exception as e:
            print(f"\n[ERROR] Neo4j写入失败")
            print(f"  原因: {e}")
            
            self.last_write_summary = {}
            return False
        
        print(f"\n{'='*60}")
        print("[SUCCESS] Write Successful!")
        print(f"{'='*60}\n")
        
        graph_nodes = self.graph.number_of_nodes()
        graph_edges = self.graph.number_of_edges()
        neo_nodes = None
        neo_edges = None
        try:
            with self.neo4j_driver.session(database=self.neo4j_database) as session:
                neo_nodes = session.run("MATCH (n) RETURN count(n) AS c").single().get("c", 0)
                neo_edges = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single().get("c", 0)
        except Exception as e:
            print(f"[WARN] Unable to fetch Neo4j counts: {e}")

        summary = {
            "graph_nodes": graph_nodes,
            "graph_edges": graph_edges,
            "neo4j_nodes": neo_nodes if neo_nodes is not None else graph_nodes,
            "neo4j_edges": neo_edges if neo_edges is not None else graph_edges,
        }

        self.last_write_summary = summary

        print("[Summary] Final graph metrics:")
        print(f"  - NetworkX nodes : {graph_nodes}")
        print(f"  - NetworkX edges : {graph_edges}")
        if neo_nodes is not None and neo_edges is not None:
            print(f"  - Neo4j nodes    : {neo_nodes}")
            print(f"  - Neo4j edges    : {neo_edges}")

        # 可选：在成功提交后，生成语义合并候选（不改图，仅输出建议）
        if self.enable_semantic_fusion:
            try:
                suggestions = self.suggest_entity_fusions(
                    ratio=self.fusion_ratio, max_pairs=self.fusion_max_pairs
                )
                if suggestions:
                    print("[Entity Fusion Suggestions]")
                    for s in suggestions:
                        print(f"  - {s['a']['name']} ({s['a']['type']})  ~~  {s['b']['name']} ({s['b']['type']})  | score={s['score']:.3f}  | context={s['context']}")
                    # 可选：强匹配对写入别名边
                    if self.auto_alias_links:
                        written = self._write_alias_edges(suggestions)
                        print(f"[Entity Fusion] Auto alias links written: {written}")
                else:
                    print("[Entity Fusion] No strong candidates found.")
            except Exception as e:
                print(f"[Entity Fusion] Failed to generate suggestions: {e}")

        return True

    # ========= 可选：语义相似度+上下文建议 =========
    def suggest_entity_fusions(self, ratio: float = 0.90, max_pairs: int = 50):
        """基于名称相似 + 上下文邻接的简易合并建议（不写库）。
        - 名称近似：difflib.SequenceMatcher 相似度 ≥ ratio
        - 上下文一致性：两实体与其他节点的关系类型或邻接集合重合 ≥ 1 则加分
        返回：[{a:{id,name,type}, b:{...}, score:float, context:str}]
        """
        # 收集同类型实体
        by_type = {}
        for node_id, data in self.graph.nodes(data=True):
            t = data.get("properties", {}).get("schema_type") or ""
            name = data.get("properties", {}).get("name") or ""
            if not name:
                continue
            by_type.setdefault(t, []).append((node_id, name))

        suggestions = []
        for t, items in by_type.items():
            # 枚举对（上限）
            for (id1, n1), (id2, n2) in itertools.combinations(items, 2):
                sim = difflib.SequenceMatcher(a=n1, b=n2).ratio()
                if sim < ratio:
                    continue
                # 简易上下文：比较出入边的关系类型集合
                rels1 = set([d.get("relation") for _, _, _, d in self.graph.edges(id1, data=True, keys=True)] +
                            [d.get("relation") for _, _, _, d in self.graph.in_edges(id1, data=True, keys=True)])
                rels2 = set([d.get("relation") for _, _, _, d in self.graph.edges(id2, data=True, keys=True)] +
                            [d.get("relation") for _, _, _, d in self.graph.in_edges(id2, data=True, keys=True)])
                overlap = len(rels1.intersection(rels2))
                score = sim + 0.02 * overlap
                suggestions.append({
                    "a": {"id": id1, "name": n1, "type": t},
                    "b": {"id": id2, "name": n2, "type": t},
                    "score": score,
                    "context": f"rel_overlap={overlap}",
                })
                if len(suggestions) >= max_pairs:
                    break
            if len(suggestions) >= max_pairs:
                break
        suggestions.sort(key=lambda x: x["score"], reverse=True)
        return suggestions

    def _write_alias_edges(self, suggestions: List[Dict[str, Any]]) -> int:
        """将高分对写入 Neo4j 的 ALIAS_OF 关系（不做节点合并）。
        规则：score>=alias_min_score 且 context 中 rel_overlap>=alias_min_overlap。
        最多写 alias_max_edges 条，避免批量误写。
        """
        strong_pairs = []
        for s in suggestions:
            if s.get("score", 0) < self.alias_min_score:
                continue
            ctx = s.get("context", "")
            try:
                # 从 "rel_overlap=X" 取数字
                overlap = int(ctx.split("rel_overlap=")[-1]) if "rel_overlap=" in ctx else 0
            except Exception:
                overlap = 0
            if overlap < self.alias_min_overlap:
                continue
            strong_pairs.append((s["a"]["id"], s["b"]["id"]))
            if len(strong_pairs) >= self.alias_max_edges:
                break

        if not strong_pairs:
            return 0

        with self.neo4j_driver.session(database=self.neo4j_database) as session:
            session.run(
                """
                UNWIND $pairs AS p
                MATCH (a {id: p.a})
                MATCH (b {id: p.b})
                MERGE (a)-[:ALIAS_OF]->(b)
                """,
                pairs=[{"a": a, "b": b} for a, b in strong_pairs]
            )
        return len(strong_pairs)

    def _auto_attach_orphan_entities(self):
        """
        自动将孤立的功能分区/空间实体连接到核心空间体系

        策略：
        1. 识别孤立节点（没有CONTAINS关系的功能分区/空间）
        2. 基于名称关键词匹配，自动挂载到预定义的核心实体
        3. 创建CONTAINS关系，标记为auto_generated
        """
        print(f"\n[INFO] Auto-attaching orphan entities to core hierarchy...")

        # 定义核心实体关键词映射（功能分区 -> 可能的父实体关键词）
        core_mappings = {
            # 急诊相关
            "急诊部": ["急救", "急诊", "抢救", "EICU", "急诊监护"],
            # 门诊相关
            "门诊部": ["门诊", "诊室", "候诊", "挂号", "分诊"],
            # 医技相关
            "手术部": ["手术", "术前", "术后", "刷手", "麻醉准备", "器械准备"],
            "ICU": ["重症", "监护", "ICU", "SICU", "NICU", "PICU"],
            "检验科": ["检验", "化验", "采血", "标本"],
            "影像中心": ["放射", "CT", "MRI", "X光", "超声", "影像"],
            "药剂科": ["药房", "药库", "配药"],
            # 住院相关
            "住院部": ["病房", "护士站", "病区", "住院"],
        }

        # 查找所有功能分区和空间节点
        orphan_nodes = []
        parent_candidates = {}

        for node_id, node_data in self.graph.nodes(data=True):
            props = node_data.get("properties", {})
            schema_type = props.get("schema_type")
            name = props.get("name", "")

            # 收集核心实体（部门/功能分区）作为潜在父节点
            if schema_type in {"部门", "功能分区"}:
                parent_candidates[node_id] = (name, schema_type)

            # 识别孤立的功能分区/空间（没有被CONTAINS的入边）
            if schema_type in {"功能分区", "空间"}:
                has_parent = False
                for _, target, edge_data in self.graph.in_edges(node_id, data=True):
                    rel_type = edge_data.get("type", "")
                    if rel_type == "CONTAINS":
                        has_parent = True
                        break

                if not has_parent:
                    orphan_nodes.append((node_id, name, schema_type))

        # 自动挂载孤立节点
        attached_count = 0
        for orphan_id, orphan_name, orphan_type in orphan_nodes:
            orphan_name_lower = orphan_name.lower()

            # 尝试匹配核心实体
            best_match = None
            best_score = 0

            for parent_id, (parent_name, parent_type) in parent_candidates.items():
                # 跳过自己
                if parent_id == orphan_id:
                    continue

                # 基于关键词匹配
                for core_name, keywords in core_mappings.items():
                    if core_name in parent_name:
                        for keyword in keywords:
                            if keyword in orphan_name_lower or keyword.lower() in orphan_name_lower:
                                # 计算匹配分数（关键词长度越长，匹配越精确）
                                score = len(keyword)
                                if score > best_score:
                                    best_score = score
                                    best_match = (parent_id, parent_name, parent_type)

            # 如果找到匹配，创建CONTAINS关系
            if best_match and best_score >= 2:  # 至少2个字符匹配
                parent_id, parent_name, parent_type = best_match

                # 检查是否已存在关系
                if not self.graph.has_edge(parent_id, orphan_id):
                    self.graph.add_edge(
                        parent_id,
                        orphan_id,
                        type="CONTAINS",
                        properties={
                            "auto_generated": True,
                            "matched_by": f"关键词匹配",
                            "script_ver": "v6.0_auto_attach",
                            "created_at": datetime.now().isoformat()
                        }
                    )
                    attached_count += 1
                    print(f"[AUTO-ATTACH] {parent_name}({parent_type}) -[CONTAINS]-> {orphan_name}({orphan_type})")

        print(f"[INFO] Auto-attached {attached_count} orphan entities to core hierarchy")
        return attached_count

    def write_to_neo4j(self):
        """将图批量写入Neo4j（UNWIND，属性作为properties，安全无动态标签）。"""
        print(f"\n{'='*60}")
        print("Writing Knowledge Graph to Neo4j (batched)")
        print(f"{'='*60}\n")

        # [NEW] 在写入Neo4j之前，自动挂载孤立实体到核心体系
        self._auto_attach_orphan_entities()

        # 准备节点数据（按真实标签分组）
        from collections import defaultdict
        nodes_by_label = defaultdict(list)
        node_label_by_id = {}
        for node_id, node_data in self.graph.nodes(data=True):
            properties = node_data.get("properties", {})
            level = node_data.get("level", 2)
            schema_type = properties.get("schema_type")
            label = self.type_to_label.get(schema_type)
            if not label:
                # 未知类型：跳过，保持图谱纯净
                continue
            # 过滤节点属性：仅保留 schema 允许的键
            allowed_keys = self.allowed_props_by_label.get(label, set())
            filtered_props = {}
            # 名称键优先
            primary_key = self.primary_name_key_by_label.get(label, "name")
            name_value = properties.get("name") or properties.get("title") or properties.get(primary_key)
            if name_value:
                filtered_props[primary_key] = name_value
            # 其它允许键
            for k in allowed_keys:
                if k in {primary_key}:  # 已处理
                    continue
                if k in properties and properties[k] is not None:
                    filtered_props[k] = properties[k]
            # 系统保留字段
            filtered_props["schema_type"] = schema_type
            filtered_props["chunk_ids"] = properties.get("chunk_ids", [])
            if self.keep_attributes_list:
                filtered_props["attributes"] = properties.get("attributes", [])
            filtered_props["level"] = level

            row = {"id": node_id, "props": filtered_props}
            nodes_by_label[label].append(row)
            node_label_by_id[node_id] = label

        with self.neo4j_driver.session(database=self.neo4j_database) as session:
            # 批量写节点（使用真实标签，而非 :Entity）
            for lbl, rows in nodes_by_label.items():
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MERGE (n:`{lbl}` {{id: row.id}})
                    ON CREATE SET
                        n += row.props,
                        n.id = row.id
                    ON MATCH SET 
                        n += row.props,
                        n.chunk_ids = CASE 
                            WHEN n.chunk_ids IS NULL THEN row.props.chunk_ids
                            ELSE n.chunk_ids + [x IN row.props.chunk_ids WHERE NOT x IN n.chunk_ids]
                        END
                    """,
                    rows=rows,
                )

            # 准备关系，按关系类型分组批量写
            # 关系：按 (关系类型, 源标签, 目标标签) 分组，使用各自标签匹配
            rel_grouped = defaultdict(lambda: defaultdict(list))
            for u, v, key, data in self.graph.edges(data=True, keys=True):
                rel_type = self._sanitize_rel_type(data.get("relation", "RELATED_TO"))
                src_lbl = node_label_by_id.get(u)
                dst_lbl = node_label_by_id.get(v)
                if not src_lbl or not dst_lbl:
                    continue
                rel_grouped[rel_type][(src_lbl, dst_lbl)].append({
                    "source": u,
                    "target": v,
                    "chunk_id": data.get("chunk_id", ""),
                    "original_relation": data.get("original_relation", ""),
                    "confidence": data.get("confidence", 0.8),
                    "is_inverse": data.get("is_inverse", False),
                })

            for rel_type, by_lbls in rel_grouped.items():
                allowed_rel_prop_keys = self.allowed_rel_props_by_type.get(rel_type, set())
                for (src_lbl, dst_lbl), rels in by_lbls.items():
                    # 预过滤关系属性，只保留 schema 允许的键
                    rows = []
                    for r in rels:
                        props = {
                            "chunk_ids": [r.get("chunk_id", "")] if r.get("chunk_id") else [],
                            "original_relation": r.get("original_relation", ""),
                            "confidence": r.get("confidence", 0.8),
                            "is_inverse": r.get("is_inverse", False),
                        }
                        system_prop_keys = {"inferred", "support", "window", "refined_by", "reason"}
                        for k in system_prop_keys:
                            if k in r and r[k] is not None:
                                props[k] = r[k]
                        for k in allowed_rel_prop_keys:
                            if k in r:
                                props[k] = r[k]
                        rows.append({
                            "source": r["source"],
                            "target": r["target"],
                            "props": props,
                        })

                    session.run(
                        f"""
                        UNWIND $rows AS r
                        MATCH (a:`{src_lbl}` {{id: r.source}})
                        MATCH (b:`{dst_lbl}` {{id: r.target}})
                        MERGE (a)-[e:`{rel_type}`]->(b)
                        ON CREATE SET e = r.props
                        ON MATCH SET 
                            e += r.props,
                            e.chunk_ids = CASE
                                WHEN e.chunk_ids IS NULL THEN r.props.chunk_ids
                                ELSE e.chunk_ids + [x IN r.props.chunk_ids WHERE NOT x IN e.chunk_ids]
                            END
                        """,
                        rows=rows,
                    )

        print(f"[OK] Knowledge graph written to Neo4j (batched)\n")
        print(f"  [OK] Entities stored with embedded attributes")
        print(f"  [OK] Chunk IDs linked for MongoDB traceability")
    
    def close(self):
        """关闭所有连接"""
        self.mongo_client.close()
        self.neo4j_driver.close()
        print("[OK] All connections closed")

    def _register_chunk_entities(self, chunk_id: str, entity_types: Dict[str, Any], chunk: Dict[str, Any]):
        """记录chunk内的实体稳定ID，用于后续共现增强。"""
        doc_id = str(chunk.get("doc_id", ""))
        self.chunk_doc_map[chunk_id] = doc_id
        self.chunk_order_map[chunk_id] = self._chunk_sequence_counter
        self._chunk_sequence_counter += 1

        entities: List[Tuple[str, str]] = []
        seen = set()
        for name, etype in (entity_types or {}).items():
            stable_id = self._generate_stable_entity_id(name, etype or "")
            if stable_id in seen:
                continue
            seen.add(stable_id)
            entities.append((stable_id, etype or ""))
        self._chunk_entity_index[chunk_id] = entities

    def _entities_of_chunk(self, chunk_id: str) -> List[Tuple[str, str]]:
        """返回指定chunk记录的实体稳定ID及类型。"""
        return self._chunk_entity_index.get(chunk_id, [])

    def _refine_relation_rule(self, subj_type: str, obj_type: str, context: str = "") -> Tuple[str, float, Dict[str, Any]]:
        """基于规则将关系细化到更具体的类型，返回(关系, 置信度, 额外属性)。"""
        txt = (context or "").lower()
        keywords_adjacent = ["毗邻", "相邻", "邻近", "邻接", "隔壁", "比邻"]
        keywords_connected = ["连通", "连接", "通向", "通过门", "连廊", "走廊", "通道"]

        if any(k in txt for k in keywords_adjacent):
            return "ADJACENT_TO", 0.8, {}
        if any(k in txt for k in keywords_connected):
            return "CONNECTED_TO", 0.8, {}

        subj_type = subj_type or ""
        obj_type = obj_type or ""

        if subj_type in {"功能分区", "部门"} and obj_type == "空间":
            return "CONTAINS", 0.8, {}
        if obj_type == "资料来源":
            return "MENTIONED_IN", 0.85, {}
        if subj_type == "设计方法" and obj_type in {"空间", "功能分区", "部门"}:
            return "GUIDES", 0.8, {}
        if subj_type == "资料来源" and obj_type == "资料来源":
            return "REFERENCES", 0.7, {}
        if subj_type in {"功能分区", "空间", "部门"} and obj_type == "医疗服务":
            return "PROVIDES", 0.78, {}
        if subj_type == "医疗服务" and obj_type in {"功能分区", "空间"}:
            return "PERFORMED_IN", 0.78, {}
        if subj_type in {"功能分区", "空间", "医疗服务"} and obj_type == "医疗设备":
            return "REQUIRES", 0.78, {}
        if subj_type == "医疗设备" and obj_type in {"功能分区", "空间"}:
            return "SUPPORTS", 0.75, {}
        if subj_type == "治疗方法" and obj_type == "医疗设备":
            return "USES", 0.8, {}
        if subj_type in {"空间", "医疗设备"} and obj_type == "治疗方法":
            return "SUPPORTS", 0.75, {}
        if subj_type == "医疗服务" and obj_type in {"空间", "功能分区"}:
            return "PERFORMED_IN", 0.78, {}

        return "CO_OCCUR", 0.5, {"inferred": True}

    def _refine_relation_llm(self, subj: str, subj_type: str, obj: str, obj_type: str, context: str = "") -> Tuple[str, float, Dict[str, Any]]:
        """通过LLM将模糊关系细化为具体关系，失败时返回CO_OCCUR。"""
        if not self.llm_client:
            return "CO_OCCUR", 0.5, {"inferred": True}

        options = [
            "MENTIONED_IN", "CONTAINS", "CONNECTED_TO", "ADJACENT_TO", "REQUIRES",
            "GUIDES", "REFERENCES", "PROVIDES", "PERFORMED_IN", "USES", "SUPPORTS",
            "CO_OCCUR"
        ]
        prompt = (
            "你是医疗建筑领域的专家。请将以下关系归类为最合理的一种关系类型，"
            "可选项：" + ", ".join(options) + "。\n"
            "如果无法确定具体语义，请返回 CO_OCCUR。\n"
            f"主体: {subj} ({subj_type})\n"
            f"客体: {obj} ({obj_type})\n"
            f"上下文: {context[:400]}\n"
            "请返回JSON：{\"relation\": 类型, \"confidence\": 0.5-0.9, \"reason\": \"简短理由\"}"
        )
        try:
            resp = self.llm_client.chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            ) or {}
            relation = str(resp.get("relation", "CO_OCCUR")).strip().upper()
            confidence = float(resp.get("confidence", 0.6))
            if relation not in options:
                relation = "CO_OCCUR"
                confidence = min(confidence, 0.6)
            extra = {
                "inferred": True,
                "refined_by": "llm",
                "reason": resp.get("reason", "")
            }
            return relation, confidence, extra
        except Exception as exc:
            print(f"[WARN] LLM relation refinement failed: {exc}")
            return "CO_OCCUR", 0.5, {"inferred": True, "refined_by": "llm_error"}

    def _refine_relation(self, subj: str, subj_type: str, obj: str, obj_type: str, original: str, context: str = "") -> Tuple[str, float, Dict[str, Any]]:
        """综合规则和LLM对模糊关系进行细化。"""
        relation, confidence, extra = self._refine_relation_rule(subj_type, obj_type, context)
        if relation != "CO_OCCUR":
            return relation, confidence, extra
        if os.getenv("KG_RELATION_REFINE_LLM", "1").lower() in {"1", "true", "yes"}:
            relation, confidence, extra_llm = self._refine_relation_llm(subj, subj_type, obj, obj_type, context)
            extra = {**extra, **extra_llm}
        return relation, confidence, extra

    def augment_relations_by_cooccurrence(self, window: int = 3, min_support: int = 2):
        """基于同文档滑动窗口的共现统计补充关系。"""
        if not self.enable_cooccurrence_aug:
            return

        doc_chunks: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
        for chunk_id, doc_id in self.chunk_doc_map.items():
            order = self.chunk_order_map.get(chunk_id, 0)
            doc_chunks[doc_id].append((order, chunk_id))

        for doc_id, chunk_pairs in doc_chunks.items():
            if not chunk_pairs:
                continue
            chunk_pairs.sort(key=lambda x: x[0])
            seq = []
            for _, chunk_id in chunk_pairs:
                ents = self._entities_of_chunk(chunk_id)
                if ents:
                    seq.append((chunk_id, ents))
            if len(seq) < 2:
                continue

            pair_counter: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(lambda: {"count": 0, "types": ("", ""), "chunks": set()})
            for idx, (chunk_id, ents) in enumerate(seq):
                if isinstance(window, int) and window < 0:
                    start, end = 0, len(seq) - 1
                else:
                    start = max(0, idx - window)
                    end = min(len(seq) - 1, idx + window)
                ents_set = list(ents)
                for j in range(start, end + 1):
                    if j == idx:
                        continue
                    other_chunk_id, other_ents = seq[j]
                    for (a_id, a_type) in ents_set:
                        for (b_id, b_type) in other_ents:
                            if a_id == b_id:
                                continue
                            key = (a_id, b_id)
                            data = pair_counter[key]
                            data["count"] += 1
                            data["types"] = (a_type, b_type)
                            data["chunks"].update({chunk_id, other_chunk_id})

            for (a_id, b_id), info in pair_counter.items():
                if info["count"] < min_support:
                    continue
                subj_type, obj_type = info["types"]
                if self.cooccur_allowed_pairs and (subj_type, obj_type) not in self.cooccur_allowed_pairs:
                    continue
                relation, confidence, extra = self._refine_relation_rule(subj_type, obj_type)
                if relation == "CO_OCCUR":
                    if not self.cooccur_write_cooccur:
                        continue
                    if info["count"] >= max(min_support, 3):
                        confidence = max(confidence, 0.6)
                elif confidence < 0.6:
                    confidence = 0.6

                existing = [
                    d.get("relation")
                    for _, v, d in self.graph.edges(a_id, data=True)
                    if v == b_id
                ]
                existing_inverse = [
                    d.get("relation")
                    for u, _, d in self.graph.edges(b_id, data=True)
                    if u == a_id
                ]
                if any(rel != "CO_OCCUR" for rel in existing + existing_inverse):
                    continue
                if relation in existing:
                    continue

                self.graph.add_edge(
                    a_id,
                    b_id,
                    relation=relation,
                    chunk_id=f"cooc:{doc_id}",        # 单个chunk_id（向后兼容）
                    chunk_ids=[f"cooc:{doc_id}"],     # 数组形式（支持多来源）
                    original_relation="CO_OCCUR",
                    confidence=confidence,
                    inferred=True,
                    support=info["count"],
                    window=window
                )

                inverse_relation = get_inverse_relation(relation)
                if inverse_relation and inverse_relation not in {"CO_OCCUR"}:
                    self.graph.add_edge(
                        b_id,
                        a_id,
                        relation=inverse_relation,
                        chunk_id=f"cooc:{doc_id}",        # 单个chunk_id（向后兼容）
                        chunk_ids=[f"cooc:{doc_id}"],     # 数组形式（支持多来源）
                        original_relation="inverse_of_CO_OCCUR",
                        confidence=confidence,
                        inferred=True,
                        support=info["count"],
                        window=window,
                        is_inverse=True
                    )

    def augment_relations_by_rules(self) -> None:
        """基于领域常识的规则补边（保守实现，未命中则静默）。"""
        try:
            rules = [
                {"from_contains": "手术", "to_exact": "刷手间", "relation": "CONNECTED_TO", "confidence": 0.7},
                {"from_contains": "ICU", "to_exact": "护士站", "relation": "ADJACENT_TO", "confidence": 0.7},
            ]

            node_id_to_name: Dict[str, str] = {}
            for node_id, data in self.graph.nodes(data=True):
                props = (data or {}).get("properties") or {}
                name = props.get("name") or props.get("title") or node_id
                node_id_to_name[node_id] = str(name)

            exact_name_to_ids: Dict[str, List[str]] = defaultdict(list)
            for nid, nm in node_id_to_name.items():
                exact_name_to_ids[nm].append(nid)

            for rule in rules:
                from_kw = rule.get("from_contains")
                to_exact = rule.get("to_exact")
                rel = rule.get("relation", "CONNECTED_TO")
                conf = float(rule.get("confidence", 0.65))
                if not from_kw or not to_exact:
                    continue
                to_ids = exact_name_to_ids.get(to_exact, [])
                if not to_ids:
                    continue
                for a_id, a_name in node_id_to_name.items():
                    if from_kw not in a_name:
                        continue
                    for b_id in to_ids:
                        if a_id == b_id:
                            continue
                        existing = [
                            d.get("relation")
                            for _, v, d in self.graph.edges(a_id, data=True)
                            if v == b_id
                        ]
                        if any(r for r in existing if r and r != "CO_OCCUR"):
                            continue
                        self.graph.add_edge(
                            a_id,
                            b_id,
                            relation=rel,
                            chunk_id="rules",        # 单个chunk_id（向后兼容）
                            chunk_ids=["rules"],     # 数组形式（支持多来源）
                            original_relation="RULES",
                            confidence=conf,
                            inferred=True,
                            support=1,
                            window=0,
                        )
                        inv = get_inverse_relation(rel)
                        if inv and inv != "CO_OCCUR":
                            self.graph.add_edge(
                                b_id,
                                a_id,
                                relation=inv,
                                chunk_id="rules",        # 单个chunk_id（向后兼容）
                                chunk_ids=["rules"],     # 数组形式（支持多来源）
                                original_relation="inverse_of_RULES",
                                confidence=conf,
                                inferred=True,
                                support=1,
                                window=0,
                                is_inverse=True,
                            )
        except Exception as exc:
            print(f"[WARN] Rules augmentation failed: {exc}")

    def _determine_build_strategy(self) -> str:
        """交互式决定构建策略：rebuild 或 incremental。"""
        default_mode = os.getenv("KG_BUILD_MODE", "prompt").lower()
        if default_mode in {"rebuild", "incremental"}:
            print(f"[INFO] Build mode via env KG_BUILD_MODE={default_mode}")
            if default_mode == "rebuild":
                self._prepare_for_rebuild()
            return default_mode

        # 提示当前状态
        print("\n=== Knowledge Graph Build Mode ===")
        try:
            with self.neo4j_driver.session(database=self.neo4j_database) as session:
                node_count = session.run("MATCH (n) RETURN count(n) AS c").single().get("c", 0)
                edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single().get("c", 0)
            print(f"Current Neo4j state: {node_count} nodes / {edge_count} edges")
        except Exception as e:
            print(f"[WARN] Unable to fetch Neo4j stats: {e}")

        print("\nChoose build strategy:")
        print("  1. Incremental (append new data)")
        print("  2. Rebuild (clear Neo4j)")
        
        # 检查是否为交互式环境
        is_interactive = sys.stdin.isatty()
        
        if not is_interactive:
            print("[INFO] Non-interactive environment detected. Defaulting to incremental mode.")
            print("[INFO] To specify build mode, set environment variable: KG_BUILD_MODE=rebuild|incremental")
            return "incremental"

        try:
            choice = input("Enter choice (1/2, default 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Input not available. Defaulting to incremental mode.")
            return "incremental"
        
        if choice == "2":
            try:
                confirm = input("This will clear existing graph. Type 'REBUILD' to confirm: ").strip()
                if confirm == "REBUILD":  # ✅ 修复：正确缩进在 try 块内
                    self._prepare_for_rebuild()
                    return "rebuild"
                print("[INFO] Rebuild cancelled. Proceeding with incremental mode.")
            except (EOFError, KeyboardInterrupt):
                print("\n[INFO] Input interrupted. Rebuild cancelled. Proceeding with incremental mode.")
        
        return "incremental"

    def _prepare_for_rebuild(self) -> None:
        """清空 Neo4j 为重建做准备。"""
        print("[INFO] Clearing Neo4j nodes and relationships...")
        try:
            with self.neo4j_driver.session(database=self.neo4j_database) as session:
                session.run("MATCH (n) DETACH DELETE n")
            print("  ✓ Neo4j cleared")
        except Exception as e:
            print(f"  ✗ Failed to clear Neo4j: {e}")

    def _update_chunk_entity_links(self):
        """
        构建完成后，批量更新MongoDB chunks的entity_ids字段

        实现chunk ↔ entity双向链接：
        - Neo4j实体节点有chunk_ids属性（在write_to_neo4j中已处理）
        - MongoDB chunks需要添加entity_ids属性
        """
        print(f"\n{'='*60}")
        print("Updating chunk-entity bidirectional links in MongoDB")
        print(f"{'='*60}\n")

        try:
            # 从Neo4j查询所有 chunk_id -> entity_ids 的映射
            with self.neo4j_driver.session(database=self.neo4j_database) as session:
                result = session.run("""
                    MATCH (e)-[r:MENTIONED_IN]->(s)
                    WHERE r.chunk_id IS NOT NULL AND r.chunk_id <> ''
                    WITH r.chunk_id AS chunk_id, collect(DISTINCT id(e)) AS entity_ids
                    RETURN chunk_id, entity_ids
                """)

                chunk_entity_map = {}
                for record in result:
                    chunk_id = record["chunk_id"]
                    entity_ids = [str(eid) for eid in record["entity_ids"]]
                    chunk_entity_map[chunk_id] = entity_ids

                print(f"[OK] Collected entity mappings for {len(chunk_entity_map)} chunks from Neo4j")

            # 批量更新MongoDB
            if not chunk_entity_map:
                print("[WARN] No chunk-entity mappings found. Skipping MongoDB update.")
                return

            from pymongo import UpdateOne
            operations = []
            for chunk_id, entity_ids in chunk_entity_map.items():
                operations.append(
                    UpdateOne(
                        {"chunk_id": chunk_id},
                        {"$set": {"entity_ids": entity_ids}}
                    )
                )

            if operations:
                result = self.chunks_collection.bulk_write(operations, ordered=False)
                print(f"[OK] Updated {result.modified_count} chunks with entity_ids in MongoDB")
                print(f"[OK] Matched {result.matched_count} chunks")
            else:
                print("[WARN] No operations to perform")

        except Exception as e:
            print(f"[FAIL] Failed to update chunk-entity links: {e}")
            import traceback
            traceback.print_exc()

    # 全局拓扑 LLM 推理已移除，避免阻塞构建
    # def _llm_infer_spatial_relation(...): pass

    # augment_spatial_topology_global 已移除，后续如需可实现为离线脚本


if __name__ == "__main__":
    # 测试构建器
    builder = MedicalKGBuilder()
    print("Builder initialized successfully!")
    builder.close()
