"""
知识图谱构建模块

包含实体提取、关系构建等核心功能

构建器特性：
- 属性内嵌存储（不再创建独立节点）
- Milvus向量存储支持
- 富媒体引用提取
- 智能体协同工作流程支持
"""

from .kg_builder import MedicalKGBuilder
from .relation_mapping import (
    normalize_relation,
    get_inverse_relation,
    classify_attribute_type
)

__all__ = [
    "MedicalKGBuilder",
    "normalize_relation",
    "get_inverse_relation",
    "classify_attribute_type",
]

