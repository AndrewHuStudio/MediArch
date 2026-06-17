"""
模块3: 知识图谱构建 -- 多阶段渐进式抽取

借鉴 Liu et al. 2026 论文方法:
  Stage 1: 多阶段 E-A 识别 (渐进式实体-属性抽取)
  Stage 2: 多阶段关系抽取 (渐进式三元组抽取)
  Stage 3: 三元组优化 (LLM 标准化)
  Stage 4: 跨文档 KG 融合 (去重 + 潜在三元组识别)
"""

import os
import math
import json
import logging
import difflib
import itertools
import hashlib
from typing import Optional, Dict, Any, List, Callable, Tuple, Set
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime

from backend.databases.graph.utils.call_llm_api import LLMClient
from backend.databases.graph.builders.kg_builder import MedicalKGBuilder
from backend.databases.graph.builders.relation_mapping import normalize_relation
from backend.databases.graph.optimization.name_normalizer import canonicalize

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class EAPair:
    """实体-属性对"""
    entity_name: str
    entity_type: str
    description: str = ""
    attributes: List[str] = field(default_factory=list)


@dataclass
class Triplet:
    """知识三元组"""
    subject: str
    relation: str
    object: str
    confidence: float = 0.85
    source_chunk_id: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    """单阶段处理结果"""
    stage: str
    rounds: int
    ea_pairs: List[EAPair] = field(default_factory=list)
    triplets: List[Triplet] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KgResult:
    """完整 KG 构建结果"""
    total_entities: int
    total_relations: int
    total_triplets: int
    nodes_written: int
    edges_written: int
    stages: List[StageResult] = field(default_factory=list)
    fusion_stats: Dict[str, Any] = field(default_factory=dict)
    quality_metrics: Dict[str, Any] = field(default_factory=dict)  # 新增质量指标


# ============================================================
# KG 模块主类
# ============================================================

class KgModule:
    """多阶段渐进式知识图谱构建模块"""

    # 阈值配置
    EA_NEW_THRESHOLD = 3       # E-A 抽取收敛阈值
    EA_MAX_ROUNDS = 5          # E-A 抽取最大轮数
    REL_NEW_THRESHOLD = 2      # 关系抽取收敛阈值
    REL_MAX_ROUNDS = 4         # 关系抽取最大轮数
    REL_ENTITY_CONTEXT_MAX_ITEMS = 60
    REL_ENTITY_CONTEXT_MAX_CHARS = 20000
    REL_CONTENT_MAX_CHARS = 12000
    FUSION_SIMILARITY = 0.90   # 实体去重相似度阈值
    LATENT_NEW_THRESHOLD = 3   # 潜在关系收敛阈值
    LATENT_MAX_ROUNDS = 4      # 潜在关系最大轮数
    FUSION_MAX_ENTITY_PAIRS = 50
    FUSION_MAX_LATENT_PAIRS = 100
    FUSION_MAX_MULTI_SOURCE = 500
    NEO4J_WRITE_CHECKPOINT_EVERY = 100
    NEO4J_WRITE_BATCH_SIZE = 100
    NEO4J_VERIFY_BATCH_SIZE = 100

    def __init__(
        self,
        strategy: str = "B1",
        custom_config: Optional[Dict[str, Any]] = None,
        relation_provider: Any = None,
    ):
        """初始化KG模块

        Args:
            strategy: 构建策略 (B0/B1/B2/B3，兼容 E1/E2/E3)
            custom_config: 自定义配置(当strategy="custom"时使用)
        """
        self.llm = LLMClient()

        # 加载策略配置
        if strategy == "custom" and custom_config:
            self.config = custom_config
        else:
            from data_process.kg.strategy_presets import get_strategy_config
            self.config = get_strategy_config(strategy)

        # 根据配置初始化Schema
        if self.config.get("use_schema", True):
            # API/runtime builds must never block on an interactive mode prompt.
            self.kg_builder = MedicalKGBuilder(build_mode="incremental")
            self.schema = self.kg_builder.schema
            try:
                self.alias_map = self.kg_builder.alias_map
            except AttributeError:
                self.alias_map = {}
        else:
            # 不使用Schema (B0策略)
            self.kg_builder = MedicalKGBuilder(build_mode="incremental")
            self.schema = {"Labels": [], "Relations": []}
            self.alias_map = {}

        # 从配置设置实验阶段开关
        self.use_schema = bool(self.config.get("use_schema", True))
        self.use_multi_round = bool(self.config.get("use_multi_round", False))
        self.use_refinement = bool(self.config.get("use_refinement", False))
        self.enable_fusion = bool(self.config.get("use_fusion", False))
        self.enable_latent_relations = bool(
            self.config.get("use_latent_relations", self.enable_fusion)
        )

        # 从配置设置迭代参数
        self.EA_MAX_ROUNDS = self.config.get("ea_max_rounds", 5)
        self.EA_NEW_THRESHOLD = self.config.get("ea_new_threshold", 3)
        self.REL_MAX_ROUNDS = self.config.get("rel_max_rounds", 4)
        self.REL_NEW_THRESHOLD = self.config.get("rel_new_threshold", 2)
        self.FUSION_SIMILARITY = self.config.get("fusion_similarity", 0.90)
        self.LATENT_MAX_ROUNDS = self._as_positive_int(
            self.config.get("latent_max_rounds", 4), default=4
        )
        self.LATENT_NEW_THRESHOLD = self._as_positive_int(
            self.config.get("latent_new_threshold", 3), default=3
        )
        self.FUSION_MAX_ENTITY_PAIRS = self._as_positive_int(
            self.config.get("fusion_max_entity_pairs", 50), default=50
        )
        self.FUSION_MAX_LATENT_PAIRS = self._as_positive_int(
            self.config.get("fusion_max_latent_pairs", 100), default=100
        )
        self.FUSION_MAX_MULTI_SOURCE = self._as_positive_int(
            self.config.get("fusion_max_multi_source", 500), default=500
        )
        self.relation_provider = relation_provider
        if self.relation_provider is None and self.config.get("use_rgcn", False):
            from data_process.kg.relation_provider import FrequencyBasedProvider
            self.relation_provider = FrequencyBasedProvider()

        self._apply_neo4j_write_tuning_from_env()
        self._configure_builder_runtime()
        self._runtime_cache_collection = self._resolve_runtime_cache_collection()
        self._active_build_signature: Optional[str] = None

        logger.info(f"KgModule initialized with strategy: {self.config.get('name', 'custom')}")

    def _resolve_runtime_cache_collection(self):
        builder = getattr(self, "kg_builder", None)
        db = getattr(builder, "db", None)
        if db is None:
            return None
        try:
            return db.get_collection("kg_runtime_stage_cache")
        except Exception:
            return None

    def _build_runtime_signature(self, chunks: List[Dict[str, Any]]) -> str:
        hasher = hashlib.sha256()
        hasher.update(str(self.config.get("name", "custom")).encode("utf-8"))
        hasher.update(str(getattr(self.llm, "model", "") or "").encode("utf-8"))
        hasher.update(str(getattr(getattr(self, "kg_builder", None), "extraction_version", "") or "").encode("utf-8"))
        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id") or chunk.get("_id") or "")
            doc_id = str(chunk.get("doc_id") or "")
            hasher.update(f"{chunk_id}|{doc_id}".encode("utf-8"))
        return hasher.hexdigest()

    @staticmethod
    def _stage_chunk_cache_key(chunk: Dict[str, Any]) -> str:
        return str(chunk.get("chunk_id") or chunk.get("_id") or "")

    def _load_runtime_stage_chunk_cache(
        self,
        stage_name: str,
        build_signature: Optional[str],
        chunk_id: str,
    ) -> Optional[Dict[str, Any]]:
        if not build_signature or not chunk_id or self._runtime_cache_collection is None:
            return None
        try:
            return self._runtime_cache_collection.find_one(
                {
                    "build_signature": str(build_signature),
                    "stage": str(stage_name),
                    "chunk_id": str(chunk_id),
                }
            )
        except Exception:
            return None

    def _save_runtime_stage_chunk_cache(
        self,
        stage_name: str,
        build_signature: Optional[str],
        chunk_id: str,
        rounds: int,
        payload: Dict[str, Any],
    ) -> None:
        if not build_signature or not chunk_id or self._runtime_cache_collection is None:
            return
        try:
            self._runtime_cache_collection.update_one(
                {
                    "build_signature": str(build_signature),
                    "stage": str(stage_name),
                    "chunk_id": str(chunk_id),
                },
                {
                    "$set": {
                        "build_signature": str(build_signature),
                        "stage": str(stage_name),
                        "chunk_id": str(chunk_id),
                        "rounds": int(rounds),
                        "payload": dict(payload or {}),
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                },
                upsert=True,
            )
        except Exception:
            pass

    def build_resume_artifacts_from_runtime_cache(
        self,
        chunks: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        build_signature = self._active_build_signature or self._build_runtime_signature(chunks)
        if not build_signature or self._runtime_cache_collection is None:
            return None

        all_ea_pairs: Dict[str, EAPair] = {}
        aggregated_triplets: Dict[str, Triplet] = {}
        stage1_rounds = 0
        stage2_rounds = 0
        eligible_chunks = 0

        for chunk in chunks:
            content = str(chunk.get("content") or "")
            if len(content) < 20:
                continue
            chunk_id = self._stage_chunk_cache_key(chunk)
            if not chunk_id:
                continue
            eligible_chunks += 1

            cached_ea = self._load_runtime_stage_chunk_cache(
                "ea_recognition", build_signature, chunk_id
            )
            cached_rel = self._load_runtime_stage_chunk_cache(
                "relation_extraction", build_signature, chunk_id
            )
            if not cached_ea or not cached_rel:
                return None

            stage1_rounds = max(stage1_rounds, int(cached_ea.get("rounds") or 0))
            stage2_rounds = max(stage2_rounds, int(cached_rel.get("rounds") or 0))

            for pair in self._deserialize_ea_pairs((cached_ea.get("payload") or {}).get("ea_pairs")):
                canonical = canonicalize(pair.entity_name, pair.entity_type, self.alias_map)
                if canonical not in all_ea_pairs:
                    all_ea_pairs[canonical] = pair
                else:
                    existing = all_ea_pairs[canonical]
                    for attr in pair.attributes:
                        if attr not in existing.attributes:
                            existing.attributes.append(attr)
                    if pair.description and len(pair.description) > len(existing.description):
                        existing.description = pair.description

            for triplet in self._deserialize_triplets((cached_rel.get("payload") or {}).get("triplets")):
                dedup_key = f"{triplet.subject}|{triplet.relation}|{triplet.object}".lower()
                if dedup_key not in aggregated_triplets:
                    aggregated_triplets[dedup_key] = triplet
                else:
                    existing = aggregated_triplets[dedup_key]
                    existing.confidence = max(float(existing.confidence), float(triplet.confidence))

        if eligible_chunks == 0 or not all_ea_pairs or not aggregated_triplets:
            return None

        return {
            "resume_from_stage": "triplet_optimization",
            "build_signature": build_signature,
            "stage1_rounds": stage1_rounds,
            "stage2_rounds": stage2_rounds,
            "ea_pairs": self._serialize_ea_pairs(list(all_ea_pairs.values())),
            "triplets": self._serialize_triplets(list(aggregated_triplets.values())),
        }

    @staticmethod
    def _serialize_ea_pairs(ea_pairs: List[EAPair]) -> List[Dict[str, Any]]:
        return [
            {
                "entity_name": pair.entity_name,
                "entity_type": pair.entity_type,
                "description": pair.description,
                "attributes": list(pair.attributes or []),
            }
            for pair in (ea_pairs or [])
        ]

    @staticmethod
    def _deserialize_ea_pairs(items: Any) -> List[EAPair]:
        pairs: List[EAPair] = []
        for item in (items or []):
            if not isinstance(item, dict):
                continue
            pairs.append(
                EAPair(
                    entity_name=str(item.get("entity_name") or ""),
                    entity_type=str(item.get("entity_type") or ""),
                    description=str(item.get("description") or ""),
                    attributes=list(item.get("attributes") or []),
                )
            )
        return pairs

    @staticmethod
    def _serialize_triplets(triplets: List[Triplet]) -> List[Dict[str, Any]]:
        return [
            {
                "subject": triplet.subject,
                "relation": triplet.relation,
                "object": triplet.object,
                "confidence": float(triplet.confidence),
                "source_chunk_id": triplet.source_chunk_id,
                "properties": dict(triplet.properties or {}),
            }
            for triplet in (triplets or [])
        ]

    @staticmethod
    def _deserialize_triplets(items: Any) -> List[Triplet]:
        triplets: List[Triplet] = []
        for item in (items or []):
            if not isinstance(item, dict):
                continue
            triplets.append(
                Triplet(
                    subject=str(item.get("subject") or ""),
                    relation=str(item.get("relation") or ""),
                    object=str(item.get("object") or ""),
                    confidence=float(item.get("confidence") or 0.85),
                    source_chunk_id=str(item.get("source_chunk_id") or ""),
                    properties=dict(item.get("properties") or {}),
                )
            )
        return triplets

    def _configure_builder_runtime(self) -> None:
        """将论文实验策略同步到底层 builder 运行配置。"""
        builder = getattr(self, "kg_builder", None)
        if builder is None:
            return

        apply_runtime_profile = getattr(builder, "apply_runtime_profile", None)
        if not callable(apply_runtime_profile):
            return

        apply_runtime_profile(
            use_schema_guidance=self.use_schema,
            ea_max_rounds=self.EA_MAX_ROUNDS,
            ea_convergence_threshold=self.EA_NEW_THRESHOLD,
            enable_rules_aug=self.use_refinement,
            enable_latent_discovery=self.enable_latent_relations,
            relation_refine_with_llm=self.use_refinement,
        )

    def _apply_neo4j_write_tuning_from_env(self) -> None:
        tuning_keys = (
            ("NEO4J_WRITE_CHECKPOINT_EVERY", "KG_NEO4J_WRITE_CHECKPOINT_EVERY"),
            ("NEO4J_WRITE_BATCH_SIZE", "KG_NEO4J_WRITE_BATCH_SIZE"),
            ("NEO4J_VERIFY_BATCH_SIZE", "KG_NEO4J_VERIFY_BATCH_SIZE"),
        )
        for attr_name, env_name in tuning_keys:
            raw = os.getenv(env_name)
            if raw is None:
                continue
            current = int(getattr(self, attr_name, 0) or 0)
            setattr(self, attr_name, self._as_positive_int(raw, default=max(1, current)))

    @staticmethod
    def _as_positive_int(raw: Any, default: int) -> int:
        try:
            value = int(raw)
        except Exception:
            return default
        return value if value > 0 else default

    def _get_schema_types(self) -> List[str]:
        """获取 schema 中定义的实体类型列表"""
        node_defs = self.schema.get("Labels") or self.schema.get("NodeConcepts") or []
        return [
            n.get("concept") or n.get("type")
            for n in node_defs
            if isinstance(n, dict) and (n.get("concept") or n.get("type"))
        ]

    def _get_relation_types(self) -> List[str]:
        """获取 schema 中定义的关系类型列表"""
        return [
            r.get("name")
            for r in (self.schema.get("Relations") or [])
            if isinstance(r, dict) and r.get("name")
        ]

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if limit <= 0 or len(text or "") <= limit:
            return text or ""
        clipped = max(0, limit - 32)
        return f"{(text or '')[:clipped]}\n...[truncated]"

    @staticmethod
    def _entity_relevance_score(content: str, pair: EAPair) -> int:
        text = content or ""
        name = str(pair.entity_name or "").strip()
        if not name:
            return 0

        score = 0
        if name in text:
            score += 1000 + min(len(name), 50)

        for attr in pair.attributes or []:
            attr_text = str(attr or "").strip()
            if attr_text and attr_text in text:
                score += 80

        description = str(pair.description or "").strip()
        if description and len(description) <= 40 and description in text:
            score += 20

        return score

    def _build_chunk_entity_context_json(self, content: str, ea_pairs: List[EAPair]) -> str:
        ranked: List[Tuple[int, str, Dict[str, Any]]] = []
        fallback: List[Tuple[str, Dict[str, Any]]] = []
        seen_names: Set[str] = set()

        for pair in ea_pairs or []:
            name = str(pair.entity_name or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            payload = {
                "type": str(pair.entity_type or ""),
                "description": str(pair.description or ""),
                "attributes": list((pair.attributes or [])[:5]),
            }
            score = self._entity_relevance_score(content, pair)
            if score > 0:
                ranked.append((score, name, payload))
            else:
                fallback.append((name, payload))

        ranked.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
        fallback.sort(key=lambda item: (len(item[0]), item[0]))

        selected: Dict[str, Dict[str, Any]] = {}
        candidates: List[Tuple[str, Dict[str, Any]]] = [
            (name, payload) for _, name, payload in ranked
        ]
        if not candidates:
            candidates = fallback[: min(12, self.REL_ENTITY_CONTEXT_MAX_ITEMS)]

        for name, payload in candidates:
            if len(selected) >= self.REL_ENTITY_CONTEXT_MAX_ITEMS:
                break
            selected[name] = payload
            rendered = json.dumps(selected, ensure_ascii=False, indent=2)
            if len(rendered) > self.REL_ENTITY_CONTEXT_MAX_CHARS:
                selected.pop(name, None)
                break

        if not selected and fallback:
            name, payload = fallback[0]
            selected[name] = payload

        if len(selected) < len(seen_names):
            logger.info(
                "Stage2 relation extraction entity context trimmed: %d -> %d",
                len(seen_names),
                len(selected),
            )

        return json.dumps(selected, ensure_ascii=False, indent=2)

    # ============================================================
    # Stage 1: 多阶段 E-A 识别
    # ============================================================

    def stage1_ea_recognition(
        self,
        chunks: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[str, int, int, int], None]] = None,
    ) -> StageResult:
        """多阶段渐进式实体-属性识别。

        对每个 chunk 迭代抽取 E-A 对，直到收敛。

        Args:
            chunks: chunk 列表，需包含 "content", "chunk_id", "content_type"
            progress_callback: fn(stage, chunk_idx, total_chunks, round_num)
        """
        all_ea_pairs: Dict[str, EAPair] = {}  # canonical_name -> EAPair
        total = len(chunks)
        last_round = 0
        cached_chunks = 0
        build_signature = self._active_build_signature or self._build_runtime_signature(chunks)
        schema_types_json = json.dumps(self._get_schema_types(), ensure_ascii=False)

        for idx, chunk in enumerate(chunks):
            content = chunk.get("content", "")
            if not content or len(content) < 20:
                continue
            content_type = chunk.get("content_type", "text")
            chunk_id = self._stage_chunk_cache_key(chunk)

            cached_doc = self._load_runtime_stage_chunk_cache(
                "ea_recognition", build_signature, chunk_id
            )
            if cached_doc:
                cached_chunks += 1
                cached_pairs = self._deserialize_ea_pairs(
                    (cached_doc.get("payload") or {}).get("ea_pairs")
                )
                last_round = max(last_round, int(cached_doc.get("rounds") or 0))
                for pair in cached_pairs:
                    canonical = canonicalize(pair.entity_name, pair.entity_type, self.alias_map)
                    if canonical not in all_ea_pairs:
                        all_ea_pairs[canonical] = pair
                    else:
                        existing = all_ea_pairs[canonical]
                        for attr in pair.attributes:
                            if attr not in existing.attributes:
                                existing.attributes.append(attr)
                        if pair.description and len(pair.description) > len(existing.description):
                            existing.description = pair.description
                continue

            chunk_ea: Dict[str, EAPair] = {}
            round_num = 0

            while round_num < self.EA_MAX_ROUNDS:
                round_num += 1
                if progress_callback:
                    progress_callback("ea_recognition", idx, total, round_num)

                prompt = self._build_ea_prompt(
                    content, content_type, schema_types_json, chunk_ea, round_num
                )
                result = self.llm.chat_json(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                if not isinstance(result, dict):
                    break

                new_entities = result.get("entities", {})
                new_attributes = result.get("attributes", {})
                new_count = 0

                for name, info in new_entities.items():
                    etype = info.get("type", "") if isinstance(info, dict) else str(info)
                    canonical = canonicalize(name, etype, self.alias_map)
                    if canonical not in chunk_ea:
                        new_count += 1
                        chunk_ea[canonical] = EAPair(
                            entity_name=name,
                            entity_type=etype,
                            description=info.get("description", "") if isinstance(info, dict) else "",
                            attributes=list(new_attributes.get(name, [])),
                        )
                    else:
                        existing = chunk_ea[canonical]
                        for attr in new_attributes.get(name, []):
                            if attr not in existing.attributes:
                                existing.attributes.append(attr)
                                new_count += 1

                if new_count < self.EA_NEW_THRESHOLD:
                    break

            last_round = max(last_round, round_num)
            self._save_runtime_stage_chunk_cache(
                "ea_recognition",
                build_signature,
                chunk_id,
                round_num,
                {"ea_pairs": self._serialize_ea_pairs(list(chunk_ea.values()))},
            )

            # 合并到全局
            for key, pair in chunk_ea.items():
                if key not in all_ea_pairs:
                    all_ea_pairs[key] = pair
                else:
                    existing = all_ea_pairs[key]
                    for attr in pair.attributes:
                        if attr not in existing.attributes:
                            existing.attributes.append(attr)
                    if pair.description and len(pair.description) > len(existing.description):
                        existing.description = pair.description

        logger.info("Stage1 E-A recognition: %d entities, %d total attributes",
                     len(all_ea_pairs),
                     sum(len(p.attributes) for p in all_ea_pairs.values()))

        return StageResult(
            stage="ea_recognition",
            rounds=last_round,
            ea_pairs=list(all_ea_pairs.values()),
            stats={
                "total_entities": len(all_ea_pairs),
                "total_attributes": sum(len(p.attributes) for p in all_ea_pairs.values()),
                "chunks_processed": total,
                "cached_chunks": cached_chunks,
            },
        )

    def _build_ea_prompt(
        self,
        content: str,
        content_type: str,
        schema_types_json: str,
        existing_ea: Dict[str, EAPair],
        round_num: int,
    ) -> str:
        """构建 E-A 抽取 prompt"""
        if round_num == 1:
            return (
                f"你是医疗建筑领域知识抽取专家。请从以下{content_type}文本中提取所有实体及其属性。\n\n"
                f"实体类型参考: {schema_types_json}\n\n"
                "提取规则:\n"
                "1. 实体名称应为简洁名词短语(2-6字)\n"
                "2. 属性应为精炼短语(不超过20字)，包含关键数值和单位\n"
                "3. 为每个实体提供一句话描述(10-30字)\n\n"
                f"文本:\n{content}\n\n"
                '输出JSON格式:\n'
                '{"entities": {"实体名": {"type": "类型", "description": "描述"}}, '
                '"attributes": {"实体名": ["属性1", "属性2"]}}'
            )

        # Round 2+: 反馈已有结果，要求补充遗漏
        existing_summary = json.dumps(
            {p.entity_name: {"type": p.entity_type, "attrs": p.attributes}
             for p in existing_ea.values()},
            ensure_ascii=False, indent=2,
        )
        return (
            "你是医疗建筑领域知识抽取专家。以下文本已经提取了一些实体和属性，"
            "请仔细检查是否遗漏了重要信息。\n\n"
            f"已提取的实体和属性:\n{existing_summary}\n\n"
            f"实体类型参考: {schema_types_json}\n\n"
            "请从以下文本中找出尚未被提取的实体和属性。注意:\n"
            "1. 检查是否有隐含的实体(如规范引用、设计方法、空间关系中的隐含实体)\n"
            "2. 检查已有实体是否缺少重要属性(面积、尺寸、标准要求等)\n"
            "3. 不要重复已提取的内容\n\n"
            f"文本:\n{content}\n\n"
            "仅输出新发现的实体和属性(JSON格式):\n"
            '{"entities": {"新实体名": {"type": "类型", "description": "描述"}}, '
            '"attributes": {"实体名": ["新属性1"]}}'
        )

    # ============================================================
    # Stage 2: 多阶段关系抽取
    # ============================================================

    def stage2_relation_extraction(
        self,
        chunks: List[Dict[str, Any]],
        ea_pairs: List[EAPair],
        progress_callback: Optional[Callable[[str, int, int, int], None]] = None,
    ) -> StageResult:
        """多阶段渐进式关系抽取。

        给定 Stage 1 的 E-A 对，对每个 chunk 迭代抽取三元组直到收敛。

        Args:
            chunks: chunk 列表
            ea_pairs: Stage 1 输出的 E-A 对
            progress_callback: fn(stage, chunk_idx, total_chunks, round_num)
        """
        relation_types_json = json.dumps(self._get_relation_types(), ensure_ascii=False)

        aggregated_triplets: Dict[str, Triplet] = {}
        total = len(chunks)
        last_round = 0
        cached_chunks = 0
        build_signature = self._active_build_signature or self._build_runtime_signature(chunks)

        for idx, chunk in enumerate(chunks):
            content = chunk.get("content", "")
            chunk_id = self._stage_chunk_cache_key(chunk)
            if not content or len(content) < 20:
                continue
            content_for_prompt = self._truncate_text(content, self.REL_CONTENT_MAX_CHARS)
            entity_list_json = self._build_chunk_entity_context_json(content_for_prompt, ea_pairs)

            cached_doc = self._load_runtime_stage_chunk_cache(
                "relation_extraction", build_signature, str(chunk_id)
            )
            if cached_doc:
                cached_chunks += 1
                cached_triplets = self._deserialize_triplets(
                    (cached_doc.get("payload") or {}).get("triplets")
                )
                last_round = max(last_round, int(cached_doc.get("rounds") or 0))
                for triplet in cached_triplets:
                    dedup_key = f"{triplet.subject}|{triplet.relation}|{triplet.object}".lower()
                    if dedup_key not in aggregated_triplets:
                        aggregated_triplets[dedup_key] = triplet
                    else:
                        existing = aggregated_triplets[dedup_key]
                        existing.confidence = max(float(existing.confidence), float(triplet.confidence))
                continue

            chunk_triplets: List[Triplet] = []
            chunk_seen: Set[str] = set()
            round_num = 0

            while round_num < self.REL_MAX_ROUNDS:
                round_num += 1
                if progress_callback:
                    progress_callback("relation_extraction", idx, total, round_num)

                prompt = self._build_relation_prompt(
                    content_for_prompt, entity_list_json, relation_types_json,
                    chunk_triplets, round_num,
                )
                result = self.llm.chat_json(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                if not isinstance(result, dict):
                    break

                raw_triples = result.get("triples", [])
                new_count = 0

                for triple in raw_triples:
                    if not isinstance(triple, (list, tuple)) or len(triple) < 3:
                        continue
                    subj, rel, obj = str(triple[0]), str(triple[1]), str(triple[2])
                    confidence = float(triple[3]) if len(triple) > 3 else 0.85

                    dedup_key = f"{subj}|{rel}|{obj}".lower()
                    if dedup_key not in chunk_seen:
                        chunk_triplets.append(Triplet(
                            subject=subj, relation=rel, object=obj,
                            confidence=confidence, source_chunk_id=chunk_id,
                        ))
                        chunk_seen.add(dedup_key)
                    doc_id = chunk.get("doc_id")
                    doc_path = chunk.get("document_path") or chunk.get("file_path")
                    if dedup_key not in aggregated_triplets:
                        new_count += 1
                        aggregated_triplets[dedup_key] = Triplet(
                            subject=subj, relation=rel, object=obj,
                            confidence=confidence,
                            source_chunk_id=chunk_id,
                            properties={
                                "doc_id": doc_id,
                                "doc_path": doc_path,
                                "support_chunk_ids": [chunk_id] if chunk_id else [],
                                "support_doc_ids": [str(doc_id)] if doc_id is not None and str(doc_id).strip() else [],
                                "support_count": 1,
                            },
                        )
                    else:
                        existing = aggregated_triplets[dedup_key]
                        existing.confidence = max(float(existing.confidence), float(confidence))
                        support_chunks = list(existing.properties.get("support_chunk_ids") or [])
                        if chunk_id and chunk_id not in support_chunks:
                            support_chunks.append(chunk_id)
                        existing.properties["support_chunk_ids"] = support_chunks

                        support_docs = list(existing.properties.get("support_doc_ids") or [])
                        doc_key = str(doc_id).strip() if doc_id is not None else ""
                        if doc_key and doc_key not in support_docs:
                            support_docs.append(doc_key)
                        existing.properties["support_doc_ids"] = support_docs
                        existing.properties["support_count"] = int(existing.properties.get("support_count", 1) or 1) + 1

                if new_count < self.REL_NEW_THRESHOLD:
                    break

            last_round = max(last_round, round_num)
            self._save_runtime_stage_chunk_cache(
                "relation_extraction",
                build_signature,
                str(chunk_id),
                round_num,
                {"triplets": self._serialize_triplets(chunk_triplets)},
            )
        all_triplets = list(aggregated_triplets.values())
        logger.info("Stage2 relation extraction: %d triplets, %d unique relations",
                     len(all_triplets), len({t.relation for t in all_triplets}))

        return StageResult(
            stage="relation_extraction",
            rounds=last_round,
            triplets=all_triplets,
            stats={
                "total_triplets": len(all_triplets),
                "unique_relations": len({t.relation for t in all_triplets}),
                "aggregated_support_count": sum(int((t.properties or {}).get("support_count", 1)) for t in all_triplets),
                "chunks_processed": total,
                "cached_chunks": cached_chunks,
            },
        )

    def _build_relation_prompt(
        self,
        content: str,
        entity_list_json: str,
        relation_types_json: str,
        existing_triplets: List[Triplet],
        round_num: int,
    ) -> str:
        """构建关系抽取 prompt"""
        if round_num == 1:
            return (
                "你是医疗建筑领域知识图谱关系抽取专家。"
                "请从以下文本中提取实体间的关系三元组。\n\n"
                f"已识别的实体上下文(类型/描述/属性):\n{entity_list_json}\n\n"
                f"可用关系类型参考: {relation_types_json}\n\n"
                "提取规则:\n"
                "1. 三元组格式: [主体, 关系, 客体, 置信度]\n"
                "2. 主体和客体应尽量来自已识别的实体列表\n"
                "3. 关系应尽量使用参考列表中的标准关系类型\n"
                "4. 置信度范围 0-1\n\n"
                f"文本:\n{content}\n\n"
                '输出JSON格式:\n{"triples": [["主体", "关系", "客体", 0.9], ...]}'
            )

        existing_summary = json.dumps(
            [[t.subject, t.relation, t.object] for t in existing_triplets],
            ensure_ascii=False,
        )
        return (
            "你是医疗建筑领域知识图谱关系抽取专家。"
            "以下文本已提取了一些关系三元组，请检查是否遗漏了重要关系。\n\n"
            f"已提取的三元组:\n{existing_summary}\n\n"
            f"已识别的实体上下文(类型/描述/属性):\n{entity_list_json}\n\n"
            f"可用关系类型参考: {relation_types_json}\n\n"
            "请从以下文本中找出尚未被提取的关系。注意:\n"
            "1. 检查隐含关系(如空间包含、功能依赖、流程顺序)\n"
            "2. 检查跨段落的间接关系\n"
            "3. 不要重复已提取的三元组\n\n"
            f"文本:\n{content}\n\n"
            '仅输出新发现的三元组(JSON格式):\n{"triples": [["主体", "关系", "客体", 0.85], ...]}'
        )

    # ============================================================
    # Stage 3: 三元组优化
    # ============================================================

    def stage3_triplet_optimization(
        self,
        triplets: List[Triplet],
        ea_pairs: List[EAPair],
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        resume_artifacts: Optional[Dict[str, Any]] = None,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> StageResult:
        """LLM 辅助的三元组标准化与优化。

        子步骤:
        a) 实体名标准化 (规则 + LLM 同义词检测)
        b) 关系类型归一化 (normalize_relation)
        c) 三元组验证与去重

        Args:
            triplets: Stage 2 输出的原始三元组
            ea_pairs: Stage 1 输出的 E-A 对 (提供实体上下文)
            progress_callback: fn(step_name, current, total)
        """
        if not triplets:
            return StageResult(stage="triplet_optimization", rounds=1, triplets=[], stats={})

        if progress_callback:
            progress_callback("optimization_start", 0, 3)

        resumed_substage = str((resume_artifacts or {}).get("substage") or "").strip()
        resumed_triplets = self._deserialize_triplets((resume_artifacts or {}).get("triplets"))
        resumed_stats = dict((resume_artifacts or {}).get("stats") or {})
        name_mapping_count = int(resumed_stats.get("names_standardized") or 0)

        # --- 子步骤 A: 实体名标准化 ---
        if resumed_substage in {"name_standardization_done", "relation_normalization_done"} and resumed_triplets:
            standardized = resumed_triplets
            if progress_callback:
                progress_callback("name_standardization_done", 1, 3)
        else:
            all_names: Set[str] = set()
            for t in triplets:
                all_names.add(t.subject)
                all_names.add(t.object)

            name_to_type: Dict[str, str] = {p.entity_name: p.entity_type for p in ea_pairs}
            name_mapping: Dict[str, str] = {}  # old_name -> canonical_name
            names_by_type: Dict[str, List[str]] = defaultdict(list)
            for name in sorted(all_names):
                names_by_type[name_to_type.get(name, "")].append(name)

            # 按实体类型分组标准化，避免把无关类型混在一起送给 LLM。
            for _, typed_names in sorted(names_by_type.items(), key=lambda item: item[0]):
                for i in range(0, len(typed_names), 30):
                    batch = typed_names[i:i + 30]
                    # 第一轮: 规则匹配
                    for name in batch:
                        etype = name_to_type.get(name, "")
                        canonical = canonicalize(name, etype, self.alias_map)
                        if canonical != name.lower().strip():
                            name_mapping[name] = canonical

                    # 第二轮: LLM 同义词检测
                    unmapped = [n for n in batch if n not in name_mapping]
                    if len(unmapped) > 1:
                        prompt = self._build_name_standardization_prompt(unmapped, name_to_type)
                        result = self.llm.chat_json(
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.0,
                        )
                        if isinstance(result, dict):
                            merges = result.get("merges", {})
                            for old_name, canonical_name in merges.items():
                                if old_name in all_names and canonical_name:
                                    name_mapping[old_name] = canonical_name

            name_mapping_count = len(name_mapping)
            if progress_callback:
                progress_callback("name_standardization_done", 1, 3)

            # 应用名称映射
            standardized: List[Triplet] = []
            for t in triplets:
                standardized.append(Triplet(
                    subject=name_mapping.get(t.subject, t.subject),
                    relation=t.relation,
                    object=name_mapping.get(t.object, t.object),
                    confidence=t.confidence,
                    source_chunk_id=t.source_chunk_id,
                    properties=t.properties,
                ))
            if checkpoint_callback:
                checkpoint_callback({
                    "substage": "name_standardization_done",
                    "triplets": self._serialize_triplets(standardized),
                    "stats": {
                        "input_triplets": len(triplets),
                        "names_standardized": name_mapping_count,
                    },
                })

        # --- 子步骤 B: 关系类型归一化 ---
        if resumed_substage == "relation_normalization_done" and resumed_triplets:
            standardized = resumed_triplets
            if progress_callback:
                progress_callback("relation_normalization_done", 2, 3)
        else:
            for t in standardized:
                normalized_rel = normalize_relation(t.relation)
                if normalized_rel and normalized_rel != "SKIP":
                    t.relation = normalized_rel

            if progress_callback:
                progress_callback("relation_normalization_done", 2, 3)
            if checkpoint_callback:
                checkpoint_callback({
                    "substage": "relation_normalization_done",
                    "triplets": self._serialize_triplets(standardized),
                    "stats": {
                        "input_triplets": len(triplets),
                        "names_standardized": name_mapping_count,
                    },
                })

        # --- 子步骤 C: 三元组验证与去重 ---
        seen: Set[str] = set()
        validated: List[Triplet] = []
        removed_count = 0

        for t in standardized:
            if t.subject == t.object:
                removed_count += 1
                continue
            if t.relation == "SKIP" or not t.relation:
                removed_count += 1
                continue
            if t.confidence < 0.3:
                removed_count += 1
                continue
            dedup_key = f"{t.subject}|{t.relation}|{t.object}".lower()
            if dedup_key in seen:
                removed_count += 1
                continue
            seen.add(dedup_key)
            validated.append(t)

        if progress_callback:
            progress_callback("validation_done", 3, 3)

        logger.info("Stage3 optimization: %d -> %d triplets (removed %d, renamed %d entities)",
                     len(triplets), len(validated), removed_count, name_mapping_count)

        return StageResult(
            stage="triplet_optimization",
            rounds=1,
            triplets=validated,
            stats={
                "input_triplets": len(triplets),
                "names_standardized": name_mapping_count,
                "output_triplets": len(validated),
                "removed": removed_count,
                "resumed_substage": resumed_substage or None,
            },
        )

    def _build_name_standardization_prompt(
        self,
        names: List[str],
        name_to_type: Dict[str, str],
    ) -> str:
        """构建实体名同义词检测 prompt"""
        names_with_types = json.dumps(
            {n: name_to_type.get(n, "") for n in names},
            ensure_ascii=False, indent=2,
        )
        return (
            "你是医疗建筑领域术语标准化专家。以下是从文档中提取的实体名称列表，"
            "请识别其中的同义词/近义词/缩写，并将它们合并为标准名称。\n\n"
            f"实体列表(名称: 类型):\n{names_with_types}\n\n"
            "规则:\n"
            "1. 仅合并确实指代同一概念的名称\n"
            "2. 标准名称应选择最规范、最完整的表述\n"
            "3. 不确定的不要合并\n\n"
            '输出JSON: {"merges": {"旧名称": "标准名称", ...}}\n'
            '如果没有需要合并的，返回 {"merges": {}}'
        )

    # ============================================================
    # Stage 4: 跨文档 KG 融合
    # ============================================================

    def stage4_cross_document_fusion(
        self,
        all_triplets: List[Triplet],
        all_ea_pairs: List[EAPair],
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        resume_artifacts: Optional[Dict[str, Any]] = None,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> StageResult:
        """跨文档 KG 融合: 去重 + 潜在三元组识别 + Neo4j 写入。

        子步骤:
        a) 跨文档实体去重 (fuzzy match + LLM 确认)
        b) 潜在三元组识别 (跨文档共现实体的隐含关系)
        c) 写入 Neo4j

        Args:
            all_triplets: Stage 3 优化后的所有三元组
            all_ea_pairs: Stage 1 的所有 E-A 对
            progress_callback: fn(step_name, current, total)
        """
        if progress_callback:
            progress_callback("fusion_start", 0, 3)

        resumed_substage = str((resume_artifacts or {}).get("substage") or "").strip()
        resumed_merge_map = {
            str(key): str(value)
            for key, value in dict((resume_artifacts or {}).get("merge_map") or {}).items()
        }
        resumed_fused_triplets = self._deserialize_triplets((resume_artifacts or {}).get("fused_triplets"))
        resumed_latent_triplets = self._deserialize_triplets((resume_artifacts or {}).get("latent_triplets"))
        resumed_final_triplets = self._deserialize_triplets((resume_artifacts or {}).get("final_triplets"))
        resumed_latent_rounds = int((resume_artifacts or {}).get("latent_rounds") or 0)
        resumed_latent_new_counts = [
            int(value or 0)
            for value in list((resume_artifacts or {}).get("latent_new_counts") or [])
        ]
        resumed_latent_progress = dict((resume_artifacts or {}).get("latent_progress") or {})

        def emit_checkpoint(payload: Dict[str, Any]) -> None:
            if checkpoint_callback:
                checkpoint_callback(dict(payload))

        emit_checkpoint({
            "substage": "fusion_start",
            "merge_map": dict(resumed_merge_map),
            "fused_triplets": self._serialize_triplets(resumed_fused_triplets),
            "latent_triplets": self._serialize_triplets(resumed_latent_triplets),
            "latent_rounds": resumed_latent_rounds,
            "latent_new_counts": list(resumed_latent_new_counts),
            "latent_candidate_pairs_total": 0,
            "final_triplets": self._serialize_triplets(resumed_final_triplets),
            "write_progress": dict((resume_artifacts or {}).get("write_progress") or {}),
        })

        enable_fusion = getattr(
            self,
            "enable_fusion",
            bool((getattr(self, "config", {}) or {}).get("use_fusion", False)),
        )
        enable_latent_relations = getattr(
            self,
            "enable_latent_relations",
            bool((getattr(self, "config", {}) or {}).get("use_latent_relations", enable_fusion)),
        )

        if not enable_fusion:
            nodes_written, edges_written = self._write_to_neo4j(
                all_triplets, all_ea_pairs, {}, progress_callback=progress_callback
            )
            return StageResult(
                stage="cross_document_fusion",
                rounds=0,
                triplets=all_triplets,
                stats={
                    "fusion_skipped": True,
                    "entities_merged": 0,
                    "latent_triplets_found": 0,
                    "latent_rounds": 0,
                    "latent_new_counts": [],
                    "total_final_triplets": len(all_triplets),
                    "nodes_written": nodes_written,
                    "edges_written": edges_written,
                },
            )

        entities_by_type: Dict[str, List[EAPair]] = defaultdict(list)
        for ea in all_ea_pairs:
            entities_by_type[ea.entity_type].append(ea)

        merge_map: Dict[str, str] = {}
        merge_count = 0

        if resumed_substage in {
            "entity_dedup_done",
            "latent_recognition",
            "latent_recognition_done",
            "neo4j_write",
        } and resumed_fused_triplets:
            merge_map = resumed_merge_map
            merge_count = len(merge_map)
            fused_triplets = resumed_fused_triplets
            if progress_callback:
                progress_callback("entity_dedup_done", 1, 3)
        else:
            for etype, entities in entities_by_type.items():
                if len(entities) < 2:
                    continue
                names = [e.entity_name for e in entities]

                candidates: List[Tuple[str, str, float]] = []
                for n1, n2 in itertools.combinations(names, 2):
                    sim = difflib.SequenceMatcher(a=n1, b=n2).ratio()
                    if sim >= self.FUSION_SIMILARITY:
                        candidates.append((n1, n2, sim))

                if not candidates:
                    continue

                pairs_for_llm = candidates[:self.FUSION_MAX_ENTITY_PAIRS]
                prompt = self._build_fusion_confirmation_prompt(pairs_for_llm, etype)
                result = self.llm.chat_json(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
                if isinstance(result, dict):
                    confirmed = result.get("confirmed_merges", [])
                    for pair in confirmed:
                        if isinstance(pair, list) and len(pair) >= 2:
                            old_name, canonical = str(pair[0]), str(pair[1])
                            merge_map[old_name] = canonical
                            merge_count += 1

            fused_triplets = []
            for t in all_triplets:
                fused_triplets.append(Triplet(
                    subject=merge_map.get(t.subject, t.subject),
                    relation=t.relation,
                    object=merge_map.get(t.object, t.object),
                    confidence=t.confidence,
                    source_chunk_id=t.source_chunk_id,
                    properties=t.properties,
                ))

            if progress_callback:
                progress_callback("entity_dedup_done", 1, 3)
            emit_checkpoint({
                "substage": "entity_dedup_done",
                "merge_map": dict(merge_map),
                "fused_triplets": self._serialize_triplets(fused_triplets),
                "latent_triplets": [],
                "latent_rounds": 0,
                "latent_new_counts": [],
                "latent_candidate_pairs_total": 0,
            })

        # --- 子步骤 B: 潜在三元组识别 ---
        entity_sources: Dict[str, Set[str]] = defaultdict(set)
        for t in fused_triplets:
            src_key = self._triplet_source_key(t)
            entity_sources[t.subject].add(src_key)
            entity_sources[t.object].add(src_key)

        existing_pairs: Set[str] = {
            f"{t.subject}|{t.object}" for t in fused_triplets
        }

        # 找跨文档共现但无直接关系的实体对
        multi_source = [
            name for name, sources in entity_sources.items()
            if len(sources) >= 2
        ]
        if len(multi_source) > self.FUSION_MAX_MULTI_SOURCE:
            multi_source = sorted(
                multi_source,
                key=lambda n: len(entity_sources.get(n, set())),
                reverse=True,
            )[:self.FUSION_MAX_MULTI_SOURCE]

        entity_pairs_without_relation: List[Tuple[str, str]] = []
        for e1, e2 in itertools.combinations(multi_source, 2):
            if (f"{e1}|{e2}" not in existing_pairs
                    and f"{e2}|{e1}" not in existing_pairs):
                shared = entity_sources[e1] & entity_sources[e2]
                if shared:
                    entity_pairs_without_relation.append((e1, e2))

        # LLM 判断隐含关系（多轮迭代 + 收敛阈值）
        name_to_type: Dict[str, str] = {}
        for etype, eas in entities_by_type.items():
            for ea in eas:
                name_to_type[ea.entity_name] = etype

        latent_triplets: List[Triplet] = list(resumed_latent_triplets) if resumed_substage in {
            "latent_recognition",
            "latent_recognition_done",
            "neo4j_write",
        } else []
        latent_seen: Set[str] = {
            f"{t.subject}|{t.relation}|{t.object}".lower() for t in (fused_triplets + latent_triplets)
        }
        latent_rounds = resumed_latent_rounds if resumed_substage in {
            "latent_recognition",
            "latent_recognition_done",
            "neo4j_write",
        } else 0
        latent_new_counts: List[int] = list(resumed_latent_new_counts) if resumed_substage in {
            "latent_recognition",
            "latent_recognition_done",
            "neo4j_write",
        } else []
        latent_candidates = entity_pairs_without_relation[:self.FUSION_MAX_LATENT_PAIRS]

        if resumed_substage in {"latent_recognition_done", "neo4j_write"}:
            if progress_callback:
                progress_callback("latent_recognition_done", 2, 3)
        elif enable_latent_relations:
            start_round = int(resumed_latent_progress.get("current_round") or 1)
            start_batch = int(resumed_latent_progress.get("next_batch_start") or 0)
            current_round_new_count = int(resumed_latent_progress.get("current_round_new_count") or 0)

            for round_num in range(start_round, self.LATENT_MAX_ROUNDS + 1):
                latent_rounds = round_num
                round_new_count = current_round_new_count if round_num == start_round else 0
                batch_start = start_batch if round_num == start_round else 0
                for i in range(batch_start, len(latent_candidates), 10):
                    batch = latent_candidates[i:i + 10]
                    candidate_relations = None
                    relation_provider = getattr(self, "relation_provider", None)
                    if relation_provider is not None:
                        candidate_relations = {}
                        existing_for_predict = fused_triplets + latent_triplets
                        for head, tail in batch:
                            try:
                                predicted = relation_provider.predict(
                                    head,
                                    tail,
                                    existing_for_predict,
                                )
                            except Exception as exc:
                                logger.warning("relation_provider.predict failed: %s", exc)
                                predicted = []
                            if predicted:
                                candidate_relations[(head, tail)] = predicted[:5]

                    prompt = self._build_latent_relation_prompt(
                        batch,
                        name_to_type,
                        existing_latent=latent_triplets,
                        candidate_relations=candidate_relations,
                    )
                    result = self.llm.chat_json(
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                    )
                    if not isinstance(result, dict):
                        continue
                    for triple in result.get("triples", []):
                        if not isinstance(triple, (list, tuple)) or len(triple) < 3:
                            continue
                        subj = str(triple[0])
                        rel = str(triple[1])
                        obj = str(triple[2])
                        dedup_key = f"{subj}|{rel}|{obj}".lower()
                        if dedup_key in latent_seen:
                            continue
                        latent_seen.add(dedup_key)
                        round_new_count += 1
                        latent_triplets.append(Triplet(
                            subject=subj,
                            relation=rel,
                            object=obj,
                            confidence=float(triple[3]) if len(triple) > 3 else 0.7,
                            source_chunk_id="latent_fusion",
                            properties={
                                "inferred": True,
                                "support_sources": sorted(
                                    list(entity_sources.get(subj, set()) & entity_sources.get(obj, set()))
                                )[:20],
                                "source_dimension": "document",
                            },
                        ))
                    emit_checkpoint({
                        "substage": "latent_recognition",
                        "merge_map": dict(merge_map),
                        "fused_triplets": self._serialize_triplets(fused_triplets),
                        "latent_triplets": self._serialize_triplets(latent_triplets),
                        "latent_rounds": max(latent_rounds, round_num - 1),
                        "latent_new_counts": list(latent_new_counts),
                        "latent_candidate_pairs_total": len(latent_candidates),
                        "latent_progress": {
                            "current_round": round_num,
                            "next_batch_start": i + len(batch),
                            "current_round_new_count": round_new_count,
                        },
                    })
                latent_new_counts.append(round_new_count)
                current_round_new_count = 0
                start_batch = 0
                if round_new_count < self.LATENT_NEW_THRESHOLD:
                    break

        all_final = fused_triplets + latent_triplets
        final_triplets = resumed_final_triplets if resumed_substage == "neo4j_write" and resumed_final_triplets else all_final

        if progress_callback:
            progress_callback("latent_recognition_done", 2, 3)
        emit_checkpoint({
            "substage": "latent_recognition_done",
            "merge_map": dict(merge_map),
            "fused_triplets": self._serialize_triplets(fused_triplets),
            "latent_triplets": self._serialize_triplets(latent_triplets),
            "latent_rounds": latent_rounds,
            "latent_new_counts": list(latent_new_counts),
            "latent_candidate_pairs_total": len(latent_candidates),
            "final_triplets": self._serialize_triplets(final_triplets),
            "write_progress": {
                "processed_count": 0,
                "verification_results": {},
                "total_triplets": len(final_triplets),
                "batches_done": 0,
                "batches_total": int(math.ceil(len(final_triplets) / max(1, int(getattr(self, "NEO4J_WRITE_BATCH_SIZE", 100) or 100)))) if final_triplets else 0,
                "write_phase": "planning_done",
            },
        })

        # --- 子步骤 C: 写入 Neo4j ---
        nodes_written, edges_written = self._write_to_neo4j(
            final_triplets,
            all_ea_pairs,
            merge_map,
            resume_artifacts=resume_artifacts if resumed_substage == "neo4j_write" else None,
            checkpoint_state={
                "substage": "neo4j_write",
                "merge_map": dict(merge_map),
                "fused_triplets": self._serialize_triplets(fused_triplets),
                "latent_triplets": self._serialize_triplets(latent_triplets),
                "latent_rounds": latent_rounds,
                "latent_new_counts": list(latent_new_counts),
                "final_triplets": self._serialize_triplets(final_triplets),
            },
            checkpoint_callback=emit_checkpoint,
            progress_callback=progress_callback,
        )

        if progress_callback:
            progress_callback("neo4j_write_done", 3, 3)

        logger.info("Stage4 fusion: merged %d entities, found %d latent triplets, "
                     "wrote %d nodes + %d edges",
                     merge_count, len(final_triplets) - len(fused_triplets), nodes_written, edges_written)
        stage_rounds = max(1, latent_rounds)

        return StageResult(
            stage="cross_document_fusion",
            rounds=stage_rounds,
            triplets=final_triplets,
            stats={
                "entities_merged": merge_count,
                "latent_triplets_found": max(0, len(final_triplets) - len(fused_triplets)),
                "latent_rounds": stage_rounds,
                "latent_new_counts": latent_new_counts,
                "total_final_triplets": len(final_triplets),
                "nodes_written": nodes_written,
                "edges_written": edges_written,
                "resumed_substage": resumed_substage or None,
            },
        )

    def _build_fusion_confirmation_prompt(
        self,
        candidates: List[Tuple[str, str, float]],
        entity_type: str,
    ) -> str:
        """构建实体合并确认 prompt"""
        pairs_json = json.dumps(
            [[c[0], c[1], round(c[2], 3)] for c in candidates],
            ensure_ascii=False,
        )
        return (
            f'你是医疗建筑领域实体消歧专家。以下是同类型("{entity_type}")的实体名称对，'
            "它们在文本相似度上较高。请判断哪些确实指代同一概念，应该合并。\n\n"
            f"候选对 [名称A, 名称B, 相似度]:\n{pairs_json}\n\n"
            "规则:\n"
            "1. 仅确认确实指代同一概念的对\n"
            "2. 合并后选择更规范的名称作为标准名\n"
            "3. 不确定的不要合并\n\n"
            '输出JSON: {"confirmed_merges": [["旧名称", "标准名称"], ...]}'
        )

    def _build_latent_relation_prompt(
        self,
        pairs: List[Tuple[str, str]],
        name_to_type: Dict[str, str],
        existing_latent: Optional[List[Triplet]] = None,
        candidate_relations: Optional[Dict[Tuple[str, str], List[Tuple[str, float]]]] = None,
    ) -> str:
        """构建潜在关系识别 prompt"""
        pairs_with_types = [
            [p[0], name_to_type.get(p[0], ""), p[1], name_to_type.get(p[1], "")]
            for p in pairs
        ]
        pairs_json = json.dumps(pairs_with_types, ensure_ascii=False)
        relation_types_json = json.dumps(self._get_relation_types(), ensure_ascii=False)
        existing_summary = ""
        if existing_latent:
            existing_summary = (
                "\n\n已识别的潜在关系(请勿重复):\n"
                + json.dumps(
                    [[t.subject, t.relation, t.object] for t in existing_latent],
                    ensure_ascii=False,
                )
            )
        candidate_summary = ""
        if candidate_relations:
            lines: List[str] = []
            for (head, tail), rels in candidate_relations.items():
                if not rels:
                    continue
                rel_text = ", ".join([f"{name}:{score:.2f}" for name, score in rels])
                lines.append(f"{head}-{tail}: {rel_text}")
            if lines:
                candidate_summary = (
                    "\n\n候选关系建议(优先考虑):\n" + "\n".join(lines)
                )

        return (
            "你是医疗建筑领域知识图谱专家。以下实体对在多个文档中共同出现，"
            "但尚未建立直接关系。请判断它们之间是否存在隐含的关系。\n\n"
            f"实体对 [实体A, 类型A, 实体B, 类型B]:\n{pairs_json}\n\n"
            f"可用关系类型: {relation_types_json}\n\n"
            f"{existing_summary}"
            f"{candidate_summary}\n\n"
            "规则:\n"
            "1. 仅输出确实存在隐含关系的对\n"
            "2. 关系必须来自可用关系类型列表\n"
            "3. 附带置信度(0-1)\n"
            "4. 不确定的不要输出\n"
            "5. 不要重复已识别的关系\n\n"
            '输出JSON: {"triples": [["实体A", "关系", "实体B", 0.75], ...]}'
        )

    def _triplet_source_key(self, triplet: Triplet) -> str:
        """优先返回文档级来源键，避免把“跨chunk”误当成“跨文档”融合。"""
        props = triplet.properties or {}
        doc_id = props.get("doc_id")
        if doc_id is not None and str(doc_id).strip():
            return f"doc:{str(doc_id).strip()}"
        doc_path = props.get("doc_path")
        if doc_path is not None and str(doc_path).strip():
            return f"path:{str(doc_path).strip()}"
        return f"chunk:{triplet.source_chunk_id}"

    # ============================================================
    # Neo4j 写入 (委托给现有 MedicalKGBuilder)
    # ============================================================

    def _write_to_neo4j(
        self,
        triplets: List[Triplet],
        ea_pairs: List[EAPair],
        merge_map: Dict[str, str],
        resume_artifacts: Optional[Dict[str, Any]] = None,
        checkpoint_state: Optional[Dict[str, Any]] = None,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[int, int]:
        """将最终三元组写入 Neo4j。

        复用 MedicalKGBuilder 的节点创建和图写入能力。

        Returns:
            (nodes_written, edges_written)
        """
        builder = self.kg_builder

        entity_types: Dict[str, str] = {}
        entity_descriptions: Dict[str, str] = {}
        for ea in ea_pairs:
            canonical = merge_map.get(ea.entity_name, ea.entity_name)
            entity_types[canonical] = ea.entity_type
            if ea.description:
                entity_descriptions[canonical] = ea.description

        try:
            with builder.lock:
                for name, etype in entity_types.items():
                    builder._find_or_create_entity(
                        entity_name=name,
                        chunk_id="kg_module_fusion",
                        entity_type=etype,
                        description=entity_descriptions.get(name),
                    )

                write_progress = dict((resume_artifacts or {}).get("write_progress") or {})
                processed_count = max(0, min(len(triplets), int(write_progress.get("processed_count") or 0)))
                verification_results = {
                    str(key): bool(value)
                    for key, value in dict(write_progress.get("verification_results") or {}).items()
                }
                semantic_verification_results = {
                    str(key): bool(value)
                    for key, value in dict(write_progress.get("semantic_verification_results") or {}).items()
                }
                semantic_classification_results: Dict[str, Dict[str, str]] = {}
                for key, value in dict(write_progress.get("semantic_classification_results") or {}).items():
                    if isinstance(value, dict):
                        semantic_classification_results[str(key)] = {
                            "action": str(value.get("action") or "review"),
                            "reason": str(value.get("reason") or ""),
                        }
                    elif isinstance(value, str):
                        semantic_classification_results[str(key)] = {
                            "action": str(value or "review"),
                            "reason": "",
                        }
                review_items_done = max(0, int(write_progress.get("review_items_done") or 0))
                review_items_total = max(0, int(write_progress.get("review_items_total") or 0))
                review_batches_done = max(0, int(write_progress.get("review_batches_done") or 0))
                review_batches_total = max(0, int(write_progress.get("review_batches_total") or 0))
                accept_count = max(0, int(write_progress.get("accept_count") or 0))
                reject_count = max(0, int(write_progress.get("reject_count") or 0))
                skip_count = max(0, int(write_progress.get("skip_count") or 0))
                checkpoint_every = max(1, int(getattr(self, "NEO4J_WRITE_CHECKPOINT_EVERY", 25) or 25))
                batch_size = max(1, int(getattr(self, "NEO4J_WRITE_BATCH_SIZE", 100) or checkpoint_every))
                verify_batch_size = max(1, int(getattr(self, "NEO4J_VERIFY_BATCH_SIZE", 50) or 50))
                total_batches = int(math.ceil(len(triplets) / batch_size)) if triplets else 0

                def _semantic_review_cache_key(triplet: Triplet, relation_name: str, subj_type: Any, obj_type: Any) -> str:
                    if hasattr(builder, "_relation_verification_cache_key"):
                        raw_key = builder._relation_verification_cache_key(
                            triplet.subject,
                            subj_type,
                            relation_name,
                            triplet.object,
                            obj_type,
                            "",
                        )
                    else:
                        raw_key = (
                            str(triplet.subject or "").strip().lower(),
                            str(subj_type or "").strip().lower(),
                            str(relation_name or "").strip().upper(),
                            str(triplet.object or "").strip().lower(),
                            str(obj_type or "").strip().lower(),
                            "",
                        )
                    return json.dumps(list(raw_key), ensure_ascii=False)

                def _classify_triplet_action(
                    triplet: Triplet,
                    relation_name: str,
                    subj_type: Any,
                    obj_type: Any,
                ) -> tuple[str, Dict[str, str]]:
                    semantic_key = _semantic_review_cache_key(triplet, relation_name, subj_type, obj_type)
                    cached = semantic_classification_results.get(semantic_key)
                    if isinstance(cached, dict) and str(cached.get("action") or "").strip():
                        return semantic_key, cached
                    classify = (
                        builder._classify_relation_verification_need(
                            triplet.subject,
                            subj_type,
                            relation_name,
                            triplet.object,
                            obj_type,
                            "",
                        )
                        if hasattr(builder, "_classify_relation_verification_need")
                        else {"action": "review", "reason": "missing_classifier"}
                    )
                    classify_payload = {
                        "action": str(classify.get("action") or "review"),
                        "reason": str(classify.get("reason") or ""),
                    }
                    semantic_classification_results[semantic_key] = classify_payload
                    return semantic_key, classify_payload

                if processed_count == 0 and review_items_total == 0 and review_batches_total == 0:
                    planned_review_items = 0
                    planned_accept = 0
                    planned_reject = 0
                    planned_skip = 0
                    planned_review_seen: Set[str] = set()
                    for triplet in triplets:
                        relation_name = normalize_relation(triplet.relation)
                        subj_type = entity_types.get(triplet.subject)
                        obj_type = entity_types.get(triplet.object)
                        semantic_key, classify = _classify_triplet_action(
                            triplet,
                            relation_name,
                            subj_type,
                            obj_type,
                        )
                        action = str(classify.get("action") or "review")
                        if action == "accept":
                            planned_accept += 1
                        elif action == "reject":
                            planned_reject += 1
                        elif action == "skip":
                            planned_skip += 1
                        else:
                            if semantic_key not in planned_review_seen:
                                planned_review_seen.add(semantic_key)
                                planned_review_items += 1
                    review_items_total = planned_review_items
                    review_batches_total = (
                        int(math.ceil(planned_review_items / verify_batch_size))
                        if planned_review_items > 0 else 0
                    )
                    accept_count = planned_accept
                    reject_count = planned_reject
                    skip_count = planned_skip

                for index, triplet in enumerate(triplets[:processed_count]):
                    override = verification_results.get(str(index))
                    builder._add_single_triple_to_graph(
                        [triplet.subject, triplet.relation, triplet.object],
                        chunk_id="kg_module_fusion",
                        entity_types=entity_types,
                        entity_descriptions=entity_descriptions,
                        verification_override=override,
                    )

                if progress_callback and triplets:
                    progress_callback("neo4j_write_progress", processed_count, len(triplets))

                for batch_start in range(processed_count, len(triplets), batch_size):
                    batch_end = min(len(triplets), batch_start + batch_size)
                    batch_triplets = triplets[batch_start:batch_end]
                    batch_overrides: Dict[int, Optional[bool]] = {}
                    llm_review_items: List[Dict[str, Any]] = []
                    llm_review_lookup: List[int] = []
                    llm_review_groups: List[List[int]] = []
                    pending_review_keys: Dict[str, int] = {}

                    for offset, triplet in enumerate(batch_triplets):
                        index = batch_start + offset
                        stored_override = verification_results.get(str(index))
                        if stored_override is not None:
                            batch_overrides[index] = bool(stored_override)
                            continue

                        relation_name = normalize_relation(triplet.relation)
                        subj_type = entity_types.get(triplet.subject)
                        obj_type = entity_types.get(triplet.object)
                        semantic_key, classify = _classify_triplet_action(
                            triplet,
                            relation_name,
                            subj_type,
                            obj_type,
                        )
                        action = str(classify.get("action") or "review")
                        if action == "accept":
                            batch_overrides[index] = True
                        elif action == "reject":
                            batch_overrides[index] = False
                        elif action == "skip":
                            batch_overrides[index] = False
                        elif action == "review":
                            cached_verdict = semantic_verification_results.get(semantic_key)
                            if cached_verdict is not None:
                                batch_overrides[index] = bool(cached_verdict)
                                verification_results[str(index)] = bool(cached_verdict)
                                continue
                            existing_group = pending_review_keys.get(semantic_key)
                            if existing_group is not None:
                                llm_review_groups[existing_group].append(index)
                                continue
                            pending_review_keys[semantic_key] = len(llm_review_items)
                            llm_review_lookup.append(index)
                            llm_review_groups.append([index])
                            llm_review_items.append({
                                "subject": triplet.subject,
                                "subject_type": subj_type,
                                "relation": relation_name,
                                "object": triplet.object,
                                "object_type": obj_type,
                                "context": "",
                                "semantic_key": semantic_key,
                            })

                    for review_start in range(0, len(llm_review_items), verify_batch_size):
                        review_batch = llm_review_items[review_start:review_start + verify_batch_size]
                        review_results = (
                            builder._llm_verify_relations_batch(review_batch)
                            if hasattr(builder, "_llm_verify_relations_batch")
                            else []
                        )
                        result_map = {
                            int(item.get("batch_index")): bool(item.get("reasonable"))
                            for item in review_results
                            if isinstance(item, dict) and item.get("batch_index") is not None
                        }
                        for local_idx, review_item in enumerate(review_batch):
                            verdict = bool(result_map.get(local_idx, False))
                            semantic_key = str(review_item.get("semantic_key") or "")
                            if semantic_key:
                                semantic_verification_results[semantic_key] = verdict
                            group_indexes = llm_review_groups[review_start + local_idx]
                            for triplet_index in group_indexes:
                                cache_key = str(triplet_index)
                                batch_overrides[triplet_index] = verdict
                                verification_results[cache_key] = verdict
                            if verdict:
                                accept_count += 1
                            else:
                                reject_count += 1
                        review_items_done += len(review_batch)
                        review_batches_done += 1

                    for offset, triplet in enumerate(batch_triplets):
                        index = batch_start + offset
                        override = batch_overrides.get(index)
                        result = builder._add_single_triple_to_graph(
                            [triplet.subject, triplet.relation, triplet.object],
                            chunk_id="kg_module_fusion",
                            entity_types=entity_types,
                            entity_descriptions=entity_descriptions,
                            verification_override=override,
                        )
                        if override is not None or result.get("verification_used"):
                            verification_results[str(index)] = bool(
                                override if override is not None else result.get("verification_result")
                            )

                    if progress_callback:
                        progress_callback("neo4j_write_progress", batch_end, len(triplets))

                    should_checkpoint = (
                        (batch_end % checkpoint_every == 0)
                        or (batch_end == len(triplets))
                    )
                    if checkpoint_callback and should_checkpoint:
                        payload = dict(checkpoint_state or {})
                        payload["substage"] = "neo4j_write"
                        payload["write_progress"] = {
                            "processed_count": batch_end,
                            "verification_results": dict(verification_results),
                            "semantic_verification_results": dict(semantic_verification_results),
                            "semantic_classification_results": dict(semantic_classification_results),
                            "review_items_done": review_items_done,
                            "review_items_total": review_items_total,
                            "review_batches_done": review_batches_done,
                            "review_batches_total": review_batches_total,
                            "accept_count": accept_count,
                            "reject_count": reject_count,
                            "skip_count": skip_count,
                            "total_triplets": len(triplets),
                            "batches_done": int(math.ceil(batch_end / batch_size)) if batch_size > 0 else 0,
                            "batches_total": total_batches,
                            "write_phase": "graph_write",
                        }
                        checkpoint_callback(payload)
                self._enrich_edge_metadata(builder, triplets, entity_types)

            builder.write_to_neo4j()
            nodes = builder.graph.number_of_nodes()
            edges = builder.graph.number_of_edges()
            return nodes, edges

        except Exception as e:
            logger.error("Neo4j write failed: %s", e)
            return 0, 0

    def _enrich_edge_metadata(self, builder: MedicalKGBuilder, triplets: List[Triplet], entity_types: Dict[str, str]) -> None:
        """将 Triplet 的来源/置信度/推断标记回填到 builder.graph 的关系上。"""
        from backend.databases.graph.builders.relation_mapping import normalize_relation as _norm_rel

        for t in triplets:
            rel = _norm_rel(t.relation)
            if not rel or rel == "SKIP":
                continue

            subj_type = entity_types.get(t.subject, "")
            obj_type = entity_types.get(t.object, "")
            try:
                subj_id = builder._generate_stable_entity_id(t.subject, subj_type)
                obj_id = builder._generate_stable_entity_id(t.object, obj_type)
            except Exception:
                continue
            if not builder.graph.has_node(subj_id) or not builder.graph.has_node(obj_id):
                continue

            edge_data = builder.graph.get_edge_data(subj_id, obj_id, default={})
            matched_attrs = None
            for _k, attrs in edge_data.items():
                if attrs.get("relation") != rel:
                    continue
                matched_attrs = attrs
                break
            if matched_attrs is None:
                continue

            matched_attrs["confidence"] = max(
                float(matched_attrs.get("confidence", 0.0) or 0.0),
                float(t.confidence or 0.0),
            )
            chunk_ids = list(matched_attrs.get("chunk_ids") or [])
            if t.source_chunk_id and t.source_chunk_id not in chunk_ids:
                chunk_ids.append(t.source_chunk_id)
            for cid in (t.properties or {}).get("support_chunk_ids", []) or []:
                if cid and cid not in chunk_ids:
                    chunk_ids.append(cid)
            matched_attrs["chunk_ids"] = chunk_ids

            props = t.properties or {}
            if bool(props.get("inferred")):
                matched_attrs["inferred"] = True
            if props.get("support_sources"):
                matched_attrs["support"] = props.get("support_sources")
            if props.get("source_dimension"):
                matched_attrs["source_dimension"] = props.get("source_dimension")
            if props.get("support_count") is not None:
                matched_attrs["support_count"] = int(props.get("support_count") or 0)

    # ============================================================
    # 主编排: 串联 4 个阶段
    # ============================================================

    def build_kg(
        self,
        chunks: List[Dict[str, Any]],
        enable_fusion: Optional[bool] = None,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        resume_artifacts: Optional[Dict[str, Any]] = None,
        stage_result_callback: Optional[Callable[[StageResult], None]] = None,
        stage_checkpoint_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> KgResult:
        """完整多阶段 KG 构建流程。

        依次执行:
        1. 多阶段 E-A 识别
        2. 多阶段关系抽取
        3. 三元组优化
        4. 跨文档融合 + Neo4j 写入

        Args:
            chunks: 所有文档的 chunk 列表
            progress_callback: fn(stage_name, step, current, total)
        """
        stages: List[StageResult] = []

        def _cb(stage: str):
            def inner(step, current, total, extra=0):
                if progress_callback:
                    progress_callback(stage, str(step), current, total)
            return inner

        logger.info("KG build start: %d chunks", len(chunks))
        self._active_build_signature = self._build_runtime_signature(chunks)

        try:
            resume_stage = str((resume_artifacts or {}).get("resume_from_stage") or "").strip()
            resumed_ea_pairs = self._deserialize_ea_pairs((resume_artifacts or {}).get("ea_pairs"))
            resumed_triplets = self._deserialize_triplets((resume_artifacts or {}).get("triplets"))

            if resume_stage in {"triplet_optimization", "cross_document_fusion"} and resumed_ea_pairs and resumed_triplets:
                stage1 = StageResult(
                    stage="ea_recognition",
                    rounds=int((resume_artifacts or {}).get("stage1_rounds") or 0),
                    ea_pairs=resumed_ea_pairs,
                    stats={
                        "resumed": True,
                        "chunks_processed": len(chunks),
                        "total_entities": len(resumed_ea_pairs),
                        "total_attributes": sum(len(p.attributes) for p in resumed_ea_pairs),
                    },
                )
                stage2 = StageResult(
                    stage="relation_extraction",
                    rounds=int((resume_artifacts or {}).get("stage2_rounds") or 0),
                    triplets=resumed_triplets,
                    stats={
                        "resumed": True,
                        "chunks_processed": len(chunks),
                        "total_triplets": len(resumed_triplets),
                        "unique_relations": len({t.relation for t in resumed_triplets}),
                    },
                )
            else:
                stage1 = self.stage1_ea_recognition(chunks, progress_callback=_cb("ea_recognition"))
                stage2 = self.stage2_relation_extraction(
                    chunks, stage1.ea_pairs, progress_callback=_cb("relation_extraction")
                )

            stages.append(stage1)
            if stage_result_callback:
                stage_result_callback(stage1)
            stages.append(stage2)
            if stage_result_callback:
                stage_result_callback(stage2)

            # Stage 3
            use_refinement = getattr(
                self,
                "use_refinement",
                bool((getattr(self, "config", {}) or {}).get("use_refinement", False)),
            )

            if resume_stage == "cross_document_fusion" and resumed_ea_pairs and resumed_triplets:
                stage3 = StageResult(
                    stage="triplet_optimization",
                    rounds=int((resume_artifacts or {}).get("stage3_rounds") or 0),
                    triplets=resumed_triplets,
                    stats={
                        "resumed": True,
                        "input_triplets": len(resumed_triplets),
                        "output_triplets": len(resumed_triplets),
                    },
                )
                stage3_triplets = stage3.triplets
            elif use_refinement:
                stage3 = self.stage3_triplet_optimization(
                    stage2.triplets,
                    stage1.ea_pairs,
                    progress_callback=_cb("triplet_optimization"),
                    resume_artifacts=dict((resume_artifacts or {}).get("stage3_checkpoint") or {}),
                    checkpoint_callback=(
                        (lambda payload: stage_checkpoint_callback("triplet_optimization", payload))
                        if stage_checkpoint_callback
                        else None
                    ),
                )
                stage3_triplets = stage3.triplets
            else:
                stage3 = StageResult(
                    stage="triplet_optimization",
                    rounds=0,
                    triplets=stage2.triplets,
                    stats={
                        "refinement_skipped": True,
                        "input_triplets": len(stage2.triplets),
                        "output_triplets": len(stage2.triplets),
                    },
                )
                stage3_triplets = stage2.triplets
            stages.append(stage3)
            if stage_result_callback:
                stage_result_callback(stage3)

            previous_fusion = getattr(
                self,
                "enable_fusion",
                bool((getattr(self, "config", {}) or {}).get("use_fusion", False)),
            )
            previous_latent = getattr(
                self,
                "enable_latent_relations",
                bool((getattr(self, "config", {}) or {}).get("use_latent_relations", previous_fusion)),
            )
            if enable_fusion is not None:
                self.enable_fusion = bool(enable_fusion)
                if not self.enable_fusion:
                    self.enable_latent_relations = False

            try:
                stage4 = self.stage4_cross_document_fusion(
                    stage3_triplets,
                    stage1.ea_pairs,
                    progress_callback=_cb("cross_document_fusion"),
                    resume_artifacts=dict((resume_artifacts or {}).get("stage4_checkpoint") or {}),
                    checkpoint_callback=(
                        (lambda payload: stage_checkpoint_callback("cross_document_fusion", payload))
                        if stage_checkpoint_callback
                        else None
                    ),
                )
            finally:
                self.enable_fusion = previous_fusion
                self.enable_latent_relations = previous_latent
            stages.append(stage4)
            if stage_result_callback:
                stage_result_callback(stage4)
        finally:
            self._active_build_signature = None

        all_entities: Set[str] = set()
        all_relations: Set[str] = set()
        for t in stage4.triplets:
            all_entities.add(t.subject)
            all_entities.add(t.object)
            all_relations.add(t.relation)

        # 计算质量指标
        quality_metrics = self._compute_quality_metrics(stage4.triplets)

        logger.info("KG build done: %d entities, %d relations, %d triplets",
                     len(all_entities), len(all_relations), len(stage4.triplets))

        return KgResult(
            total_entities=len(all_entities),
            total_relations=len(all_relations),
            total_triplets=len(stage4.triplets),
            nodes_written=stage4.stats.get("nodes_written", 0),
            edges_written=stage4.stats.get("edges_written", 0),
            stages=stages,
            fusion_stats=stage4.stats,
            quality_metrics=quality_metrics,
        )

    def _compute_quality_metrics(self, triplets: List[Triplet]) -> Dict[str, Any]:
        """计算知识图谱质量指标

        Args:
            triplets: 三元组列表

        Returns:
            质量指标字典,包含:
            - aof: 关系平均出现频率(越低越好,说明关系分布更均匀)
            - unique_entities: 唯一实体数
            - unique_relations: 唯一关系数
            - relation_diversity: 关系多样性(unique_relations / total_triplets)
            - relation_distribution: 关系分布(top 20)
        """
        from collections import Counter

        if not triplets:
            return {
                "aof": 0.0,
                "unique_entities": 0,
                "unique_relations": 0,
                "relation_diversity": 0.0,
                "relation_distribution": {},
            }

        # 统计关系分布
        rel_counts = Counter(t.relation for t in triplets)
        unique_rels = len(rel_counts)

        # 计算AOF (Average Occurrence Frequency)
        aof = sum(rel_counts.values()) / unique_rels if unique_rels else 0

        # 统计唯一实体数
        unique_entities = len(set(t.subject for t in triplets) | set(t.object for t in triplets))

        # 计算关系多样性
        relation_diversity = unique_rels / len(triplets) if triplets else 0

        return {
            "aof": round(aof, 2),
            "unique_entities": unique_entities,
            "unique_relations": unique_rels,
            "relation_diversity": round(relation_diversity, 4),
            "relation_distribution": dict(rel_counts.most_common(20)),
        }

