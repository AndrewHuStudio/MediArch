"""
kg_builder.py 单元测试模块

测试范围:
1. _normalize_entity_type_value - 实体类型归一化
2. _filter_and_normalize_entities - 批量实体过滤
3. 边界条件与异常情况处理

运行方式:
  pytest backend/databases/graph/tests/test_kg_builder.py -v
  pytest backend/databases/graph/tests/test_kg_builder.py::TestEntityNormalization -v
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any
import pytest
from unittest.mock import MagicMock, patch

# 添加项目根目录到路径（必须在导入 backend 模块之前）
# 路径层级: test_kg_builder.py -> tests -> graph -> databases -> backend -> 项目根目录
project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

from backend.databases.graph.builders.kg_builder import MedicalKGBuilder



@pytest.fixture
def builder():
    """创建测试用的 KGBuilder 实例（禁用 Milvus）"""
    with patch('backend.databases.graph.builders.kg_builder.MongoClient'):
        with patch('backend.databases.graph.builders.kg_builder.GraphDatabase'):
            builder = MedicalKGBuilder(use_milvus=False)
            # 禁用 LLM 调用
            builder.entity_type_llm_fallback = False
            yield builder
            # 清理
            if hasattr(builder, 'close'):
                try:
                    builder.close()
                except Exception:
                    pass


class TestEntityNormalization:
    """测试实体类型归一化逻辑"""

    def test_normalize_basic_type(self, builder):
        """测试基础类型：已在 schema 中"""
        normalized, original = builder._normalize_entity_type_value(
            name="手术间",
            raw_type="空间",
            content="手术间面积30平米"
        )
        assert normalized == "空间"
        assert original == "空间"

    def test_normalize_synonym_mapping_functional_unit(self, builder):
        """测试同义词映射：功能单元 → 功能分区"""
        normalized, original = builder._normalize_entity_type_value(
            name="急救中心",
            raw_type="功能单元",
            content="急救中心位于一层"
        )
        assert normalized == "功能分区"
        assert original == "功能单元"

    def test_normalize_label_to_concept_mapping(self, builder):
        """测试英文标签映射：FunctionalZone → 功能分区"""
        normalized, original = builder._normalize_entity_type_value(
            name="手术部",
            raw_type="FunctionalZone",
            content="手术部包含10个手术间"
        )
        assert normalized == "功能分区"
        assert original == "FunctionalZone"

    def test_normalize_label_to_concept_space(self, builder):
        """测试英文标签映射：Space → 空间"""
        normalized, original = builder._normalize_entity_type_value(
            name="护士站",
            raw_type="Space",
            content="护士站位于病区中心"
        )
        assert normalized == "空间"
        assert original == "Space"

    def test_normalize_special_case_department(self, builder):
        """测试特殊情况：科室 → 部门"""
        normalized, original = builder._normalize_entity_type_value(
            name="心内科",
            raw_type="科室",
            content="心内科病房"
        )
        assert normalized == "部门"
        assert original == "科室"

    def test_normalize_soft_mode_inference_space(self, builder):
        """测试软模式推理：根据"间"后缀推断为空间"""
        builder.schema_mode_soft = True
        normalized, original = builder._normalize_entity_type_value(
            name="观察间",
            raw_type="未知类型",
            content="观察间用于短期观察患者"
        )
        assert normalized == "空间"
        assert original == "未知类型"

    def test_normalize_soft_mode_inference_room(self, builder):
        """测试软模式推理：根据"室"后缀推断为空间"""
        builder.schema_mode_soft = True
        normalized, original = builder._normalize_entity_type_value(
            name="治疗室",
            raw_type="未知类型",
            content="治疗室配备完善"
        )
        assert normalized == "空间"
        assert original == "未知类型"

    def test_normalize_soft_mode_inference_zone(self, builder):
        """测试软模式推理：根据"区"推断为功能分区"""
        builder.schema_mode_soft = True
        normalized, original = builder._normalize_entity_type_value(
            name="急救区",
            raw_type="未知类型",
            content="急救区包含多个抢救室"
        )
        assert normalized == "功能分区"
        assert original == "未知类型"

    def test_normalize_soft_mode_inference_department(self, builder):
        """测试软模式推理：根据"部"推断为功能分区"""
        builder.schema_mode_soft = True
        normalized, original = builder._normalize_entity_type_value(
            name="检验部",
            raw_type="未知类型",
            content="检验部负责各类化验"
        )
        assert normalized == "功能分区"
        assert original == "未知类型"

    def test_normalize_empty_type(self, builder):
        """测试空类型：启用软模式兜底"""
        builder.schema_mode_soft = True
        normalized, original = builder._normalize_entity_type_value(
            name="诊室",
            raw_type="",
            content="诊室面积15平米"
        )
        assert normalized == "空间"  # 根据"室"推断
        assert original is None

    def test_normalize_none_type(self, builder):
        """测试 None 类型：启用软模式兜底"""
        builder.schema_mode_soft = True
        normalized, original = builder._normalize_entity_type_value(
            name="护士站",
            raw_type=None,
            content="护士站位于病区中心"
        )
        assert normalized == "空间"  # 根据"站"推断
        assert original is None

    def test_normalize_unknown_type_no_soft_mode(self, builder):
        """测试未知类型兜底：禁用软模式，返回 None"""
        builder.schema_mode_soft = False
        builder.entity_type_llm_fallback = False
        normalized, original = builder._normalize_entity_type_value(
            name="测试实体",
            raw_type="完全未知的类型",
            content="测试内容"
        )
        assert normalized is None
        assert original == "完全未知的类型"

    def test_normalize_unknown_name_no_soft_mode(self, builder):
        """测试未知名称无法推断：禁用软模式，返回 None"""
        builder.schema_mode_soft = False
        normalized, original = builder._normalize_entity_type_value(
            name="XYZABC",  # 无法推断的名称
            raw_type="未知类型",
            content="测试内容"
        )
        assert normalized is None
        assert original == "未知类型"

    def test_normalize_chain_mapping(self, builder):
        """测试链式映射：同义词 + 标签映射"""
        # 模拟一个同时需要同义词映射和标签映射的情况
        # 虽然实际上这种情况不太常见，但测试逻辑健壮性
        builder.type_synonyms["旧类型"] = "FunctionalZone"
        normalized, original = builder._normalize_entity_type_value(
            name="某区域",
            raw_type="旧类型",
            content="某区域描述"
        )
        assert normalized == "功能分区"
        assert original == "旧类型"


class TestFilterAndNormalizeEntities:
    """测试批量实体过滤"""

    def test_filter_basic_entities(self, builder):
        """测试基础实体过滤"""
        raw_types = {
            "手术间": "空间",
            "急救区": "功能分区",
            "门诊部": "部门"
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_001"
        )
        assert len(result) == 3
        assert result["手术间"] == "空间"
        assert result["急救区"] == "功能分区"
        assert result["门诊部"] == "部门"

    def test_filter_with_synonym_mapping(self, builder):
        """测试包含同义词映射的批量过滤"""
        raw_types = {
            "手术间": "空间",
            "急救中心": "功能单元",  # 应映射为功能分区
            "心内科": "科室",  # 应映射为部门
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_002"
        )
        assert len(result) == 3
        assert result["手术间"] == "空间"
        assert result["急救中心"] == "功能分区"
        assert result["心内科"] == "部门"

    def test_filter_with_label_mapping(self, builder):
        """测试包含英文标签映射的批量过滤"""
        raw_types = {
            "手术间": "Space",
            "急救区": "FunctionalZone",
            "门诊部": "DepartmentGroup"
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_003"
        )
        assert len(result) == 3
        assert result["手术间"] == "空间"
        assert result["急救区"] == "功能分区"
        assert result["门诊部"] == "部门"

    def test_filter_with_invalid_type(self, builder):
        """测试过滤无效类型"""
        builder.schema_mode_soft = False  # 禁用软模式
        raw_types = {
            "手术间": "空间",
            "测试实体": "无效类型",  # 应被过滤
            "急救区": "功能分区"
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_004"
        )
        assert len(result) == 2
        assert "手术间" in result
        assert "急救区" in result
        assert "测试实体" not in result

    def test_filter_with_soft_mode_inference(self, builder):
        """测试软模式推理批量过滤"""
        builder.schema_mode_soft = True
        raw_types = {
            "手术间": "未知类型",  # 应推断为空间
            "急救区": "未知类型",  # 应推断为功能分区
            "检验部": "未知类型",  # 应推断为功能分区
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_005"
        )
        # 软模式推理结果可能不同，只验证关键实体存在且类型正确
        assert len(result) >= 3
        assert result["手术间"] == "空间"
        assert result["急救区"] == "功能分区"
        assert result["检验部"] == "功能分区"

    def test_filter_empty_input(self, builder):
        """测试空输入"""
        result = builder._filter_and_normalize_entities(
            {},
            content="测试内容",
            chunk_id="test_chunk_006"
        )
        assert len(result) == 0

    def test_filter_none_input(self, builder):
        """测试 None 输入"""
        result = builder._filter_and_normalize_entities(
            None,
            content="测试内容",
            chunk_id="test_chunk_007"
        )
        assert len(result) == 0

    def test_filter_all_invalid(self, builder):
        """测试全部无效实体"""
        builder.schema_mode_soft = False
        raw_types = {
            "实体1": "无效类型1",
            "实体2": "无效类型2",
            "实体3": "无效类型3"
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_008"
        )
        assert len(result) == 0

    def test_filter_mixed_valid_invalid(self, builder):
        """测试混合有效和无效实体"""
        builder.schema_mode_soft = True
        raw_types = {
            "手术间": "空间",  # 有效
            "急救区": "功能分区",  # 有效
            "护士站": "未知",  # 可推断为空间
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_009"
        )
        # 验证至少包含明确有效的实体
        assert len(result) >= 3
        assert "手术间" in result
        assert "急救区" in result
        assert "护士站" in result


class TestEdgeCases:
    """测试边界情况"""

    def test_normalize_whitespace_handling(self, builder):
        """测试空格处理"""
        normalized, original = builder._normalize_entity_type_value(
            name="手术间",
            raw_type="  空间  ",  # 前后有空格
            content="测试"
        )
        assert normalized == "空间"
        assert original == "空间"

    def test_normalize_case_sensitivity(self, builder):
        """测试大小写敏感性"""
        # 中文类型应该大小写不敏感
        normalized, original = builder._normalize_entity_type_value(
            name="手术间",
            raw_type="Space",  # 英文标签
            content="测试"
        )
        assert normalized == "空间"
        assert original == "Space"

    def test_normalize_numeric_type(self, builder):
        """测试数字类型输入"""
        normalized, original = builder._normalize_entity_type_value(
            name="手术间",
            raw_type=123,  # 数字
            content="测试"
        )
        # 应该被转换为字符串"123"，然后作为未知类型处理
        assert normalized is None or normalized in builder.allowed_entity_types
        assert original == "123"

    def test_filter_duplicate_entities(self, builder):
        """测试重复实体"""
        raw_types = {
            "手术间": "空间",
            "手术间": "Space",  # 重复的key会被覆盖
        }
        result = builder._filter_and_normalize_entities(
            raw_types,
            content="测试内容",
            chunk_id="test_chunk_010"
        )
        # Python dict会自动去重，只保留最后一个
        assert len(result) == 1
        assert result["手术间"] == "空间"


if __name__ == "__main__":
    """支持直接运行测试"""
    import subprocess

    print("[INFO] Running kg_builder unit tests...")
    print("-" * 60)

    # 运行 pytest
    result = subprocess.run(
        ["pytest", __file__, "-v", "--tb=short"],
        capture_output=False
    )

    sys.exit(result.returncode)
