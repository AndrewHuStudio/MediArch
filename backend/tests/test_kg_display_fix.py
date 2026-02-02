# backend/tests/test_kg_display_fix.py
"""
测试知识图谱显示修复

验证:
1. 节点类型映射是否正确
2. 图谱构建逻辑是否使用 schema 定义的标签
3. 层级结构是否完整
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.agents.base_agent import AgentItem
from app.agents.knowledge_fusion.fusion import (
    build_answer_graph_data,
    _infer_node_type_from_neo4j,
    _infer_node_type_from_name,
    _infer_source_type,
)


def test_node_type_inference():
    """测试节点类型推断"""
    print("\n[TEST] 测试节点类型推断")
    print("=" * 60)

    # 测试从 Neo4j AgentItem 推断类型
    test_cases = [
        (AgentItem(name="综合医院", entity_type="Hospital"), "Hospital"),
        (AgentItem(name="急诊部", entity_type="DepartmentGroup"), "DepartmentGroup"),
        (AgentItem(name="手术部", entity_type="FunctionalZone"), "FunctionalZone"),
        (AgentItem(name="手术室", entity_type="Space"), "Space"),
        (AgentItem(name="三区划分法", entity_type="DesignMethod"), "DesignMethod"),
    ]

    for item, expected_type in test_cases:
        result = _infer_node_type_from_neo4j(item)
        status = "[OK]" if result == expected_type else "[FAIL]"
        print(f"{status} {item.name} -> {result} (expected: {expected_type})")

    # 测试从名称推断类型
    print("\n测试从名称推断类型:")
    name_test_cases = [
        ("综合医院", "Hospital"),
        ("门诊部", "DepartmentGroup"),
        ("急救区", "FunctionalZone"),
        ("抢救室", "Space"),
        ("双走廊设计", "DesignMethod"),
    ]

    for name, expected_type in name_test_cases:
        result = _infer_node_type_from_name(name)
        status = "[OK]" if result == expected_type else "[FAIL]"
        print(f"{status} {name} -> {result} (expected: {expected_type})")


def test_source_type_inference():
    """测试资料类型推断"""
    print("\n[TEST] 测试资料类型推断")
    print("=" * 60)

    test_cases = [
        ("GB 51039-2014 综合医院建筑设计规范", "规范标准"),
        ("医疗建筑设计详图集", "图集书籍"),
        ("医院建筑设计指南", "政策文件"),
        ("医疗建筑设计研究论文", "学术文献"),
        ("某医院项目设计文档", "项目文档"),
    ]

    for source_name, expected_type in test_cases:
        result = _infer_source_type(source_name)
        status = "[OK]" if result == expected_type else "[FAIL]"
        print(f"{status} {source_name} -> {result} (expected: {expected_type})")


def test_graph_building():
    """测试图谱构建"""
    print("\n[TEST] 测试图谱构建")
    print("=" * 60)

    # 模拟 Neo4j 返回的数据
    neo4j_items = [
        AgentItem(
            name="手术室",
            entity_type="Space",
            entity_id="space_001",
            attrs={"area_m2": 50, "clean_level": "I级"},
            score=0.95,
            edges=[
                {
                    "target": "手术部",
                    "target_id": "zone_001",
                    "target_label": "FunctionalZone",
                    "type": "CONTAINED_IN",
                }
            ],
        ),
    ]

    # 模拟 Milvus 返回的数据
    milvus_items = [
        AgentItem(
            name="手术室设计要求",
            attrs={"source_document": "GB 51039-2014 综合医院建筑设计规范"},
            score=0.88,
            citations=[
                {
                    "chunk_id": "chunk_001",
                    "source": "GB 51039-2014 综合医院建筑设计规范",
                    "page_number": 42,
                    "snippet": "手术室应设置在洁净区...",
                }
            ],
        ),
    ]

    # 构建图谱
    graph_data = build_answer_graph_data(neo4j_items, milvus_items, "手术室设计要求")

    print(f"\n图谱统计:")
    print(f"  节点数: {len(graph_data.nodes)}")
    print(f"  边数: {len(graph_data.edges)}")
    print(f"  引用数: {len(graph_data.citations)}")

    print(f"\n节点类型分布:")
    type_counts = {}
    for node in graph_data.nodes:
        type_counts[node.type] = type_counts.get(node.type, 0) + 1

    for node_type, count in sorted(type_counts.items()):
        print(f"  {node_type}: {count}")

    print(f"\n节点详情:")
    for node in graph_data.nodes:
        print(f"  [{node.type}] {node.name} (id: {node.id})")

    print(f"\n边详情:")
    for edge in graph_data.edges:
        print(f"  {edge.source} --[{edge.relation}]--> {edge.target}")

    # 验证节点类型是否符合 schema
    schema_types = [
        "Hospital", "DepartmentGroup", "FunctionalZone", "Space",
        "DesignMethod", "DesignMethodCategory", "Case", "Source"
    ]

    invalid_types = [n.type for n in graph_data.nodes if n.type not in schema_types]
    if invalid_types:
        print(f"\n[FAIL] 发现不符合 schema 的节点类型: {set(invalid_types)}")
    else:
        print(f"\n[OK] 所有节点类型都符合 schema 定义")


def test_hierarchy_enhancement():
    """测试层级结构增强"""
    print("\n[TEST] 测试层级结构增强")
    print("=" * 60)

    # 测试没有 Neo4j 数据时的兜底逻辑
    neo4j_items = []
    milvus_items = [
        AgentItem(
            name="手术室设计",
            attrs={"source_document": "医院建筑设计规范"},
            score=0.85,
        ),
    ]

    graph_data = build_answer_graph_data(neo4j_items, milvus_items, "手术室设计要求")

    print(f"\n图谱统计:")
    print(f"  节点数: {len(graph_data.nodes)}")
    print(f"  边数: {len(graph_data.edges)}")

    # 检查是否有层级结构
    has_hospital = any(n.type == "Hospital" for n in graph_data.nodes)
    has_department = any(n.type == "DepartmentGroup" for n in graph_data.nodes)
    has_zone = any(n.type == "FunctionalZone" for n in graph_data.nodes)
    has_space = any(n.type == "Space" for n in graph_data.nodes)

    print(f"\n层级结构检查:")
    print(f"  [{'OK' if has_hospital else 'FAIL'}] Hospital 节点")
    print(f"  [{'OK' if has_department else 'FAIL'}] DepartmentGroup 节点")
    print(f"  [{'OK' if has_zone else 'FAIL'}] FunctionalZone 节点")
    print(f"  [{'OK' if has_space else 'FAIL'}] Space 节点")

    if has_hospital and has_department and has_zone and has_space:
        print(f"\n[OK] 层级结构完整")
    else:
        print(f"\n[FAIL] 层级结构不完整")


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("知识图谱显示修复测试")
    print("=" * 60)

    try:
        test_node_type_inference()
        test_source_type_inference()
        test_graph_building()
        test_hierarchy_enhancement()

        print("\n" + "=" * 60)
        print("[SUCCESS] 所有测试完成")
        print("=" * 60)

    except Exception as e:
        print(f"\n[ERROR] 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
