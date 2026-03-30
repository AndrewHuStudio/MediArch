"""
知识图谱构建策略预设配置

论文实验主线统一使用 B0-B3:
- B0: 纯 LLM baseline
- B1: B0 + schema guidance
- B2: B1 + multi-round extraction
- B3: B2 + refinement + cross-document fusion + latent relation discovery

兼容旧别名:
- E1 -> B1
- E2 -> B2
- E3 -> B3
"""

from typing import Any, Dict


PAPER_STRATEGY_PRESETS: Dict[str, Dict[str, Any]] = {
    "B0": {
        "name": "B0_纯LLM基线",
        "description": "单轮抽取, 无 Schema 约束, 无 refinement, 无融合",
        "use_schema": False,
        "use_multi_round": False,
        "use_refinement": False,
        "use_fusion": False,
        "use_latent_relations": False,
        "use_rgcn": False,
        "use_prompt_optimization": False,
        "ea_max_rounds": 1,
        "rel_max_rounds": 1,
        "latent_max_rounds": 1,
        "ea_new_threshold": 3,
        "rel_new_threshold": 2,
        "latent_new_threshold": 3,
        "fusion_max_entity_pairs": 50,
        "fusion_max_latent_pairs": 100,
        "fusion_max_multi_source": 500,
        "fusion_similarity": 0.90,
    },
    "B1": {
        "name": "B1_Schema约束",
        "description": "B0 + Schema 约束",
        "use_schema": True,
        "use_multi_round": False,
        "use_refinement": False,
        "use_fusion": False,
        "use_latent_relations": False,
        "use_rgcn": False,
        "use_prompt_optimization": False,
        "ea_max_rounds": 1,
        "rel_max_rounds": 1,
        "latent_max_rounds": 1,
        "ea_new_threshold": 3,
        "rel_new_threshold": 2,
        "latent_new_threshold": 3,
        "fusion_max_entity_pairs": 50,
        "fusion_max_latent_pairs": 100,
        "fusion_max_multi_source": 500,
        "fusion_similarity": 0.90,
    },
    "B2": {
        "name": "B2_多轮抽取",
        "description": "B1 + 多轮实体属性/关系抽取",
        "use_schema": True,
        "use_multi_round": True,
        "use_refinement": False,
        "use_fusion": False,
        "use_latent_relations": False,
        "use_rgcn": False,
        "use_prompt_optimization": False,
        "ea_max_rounds": 5,
        "rel_max_rounds": 4,
        "latent_max_rounds": 1,
        "ea_new_threshold": 3,
        "rel_new_threshold": 2,
        "latent_new_threshold": 3,
        "fusion_max_entity_pairs": 50,
        "fusion_max_latent_pairs": 100,
        "fusion_max_multi_source": 500,
        "fusion_similarity": 0.90,
    },
    "B3": {
        "name": "B3_完整方案",
        "description": "B2 + refinement + 跨文档融合 + 潜在关系发现",
        "use_schema": True,
        "use_multi_round": True,
        "use_refinement": True,
        "use_fusion": True,
        "use_latent_relations": True,
        "use_rgcn": False,
        "use_prompt_optimization": False,
        "ea_max_rounds": 5,
        "rel_max_rounds": 4,
        "latent_max_rounds": 4,
        "ea_new_threshold": 3,
        "rel_new_threshold": 2,
        "latent_new_threshold": 3,
        "fusion_max_entity_pairs": 50,
        "fusion_max_latent_pairs": 100,
        "fusion_max_multi_source": 500,
        "fusion_similarity": 0.90,
    },
}

LEGACY_STRATEGY_ALIASES = {
    "E1": "B1",
    "E2": "B2",
    "E3": "B3",
}


def normalize_strategy_id(strategy: str) -> str:
    """将旧策略 ID 归一到论文实验组 ID。"""
    strategy = (strategy or "").strip()
    return LEGACY_STRATEGY_ALIASES.get(strategy, strategy)


def get_strategy_config(strategy: str) -> Dict[str, Any]:
    """获取策略配置，兼容旧别名。"""
    requested_id = (strategy or "").strip()
    canonical_id = normalize_strategy_id(requested_id)

    if canonical_id not in PAPER_STRATEGY_PRESETS:
        raise ValueError(
            f"Unknown strategy: {strategy}. "
            f"Available strategies: {list(PAPER_STRATEGY_PRESETS.keys()) + list(LEGACY_STRATEGY_ALIASES.keys())}"
        )

    config = PAPER_STRATEGY_PRESETS[canonical_id].copy()
    config["canonical_id"] = canonical_id
    config["requested_id"] = requested_id or canonical_id
    return config


def list_strategies() -> list:
    """列出前端可见的论文实验策略。"""
    return [
        {
            "id": key,
            "name": config["name"],
            "description": config["description"],
            "disabled": config.get("disabled", False),
            "defaults": {
                "ea_max_rounds": config["ea_max_rounds"],
                "ea_threshold": config["ea_new_threshold"],
                "rel_max_rounds": config["rel_max_rounds"],
                "rel_threshold": config["rel_new_threshold"],
            },
        }
        for key, config in PAPER_STRATEGY_PRESETS.items()
    ]
