#!/usr/bin/env python3
# backend/tests/test_kg_types_simple.py
"""
简单测试：验证节点类型映射

不依赖完整的模块导入，直接测试类型推断逻辑
"""


def test_node_type_mapping():
    """测试节点类型映射逻辑"""
    print("\n[TEST] 节点类型映射测试")
    print("=" * 60)

    # 模拟前端的 mapNodeType 函数
    def mapNodeType(type_str):
        if not type_str:
            return "entity"

        schema_types = [
            "Hospital", "DepartmentGroup", "FunctionalZone", "Space",
            "DesignMethod", "DesignMethodCategory", "Case", "Source",
            "MedicalService", "MedicalEquipment", "TreatmentMethod"
        ]

        if type_str in schema_types:
            return type_str

        type_lower = type_str.lower()

        if "hospital" in type_lower:
            return "Hospital"
        if "department" in type_lower:
            return "DepartmentGroup"
        if "zone" in type_lower or "功能分区" in type_lower:
            return "FunctionalZone"
        if "space" in type_lower or "room" in type_lower:
            return "Space"
        if "design" in type_lower and "method" in type_lower:
            return "DesignMethod"
        if "case" in type_lower or "案例" in type_lower:
            return "Case"
        # 注意：这里要先检查 document，因为 source 可能不在类型名中
        if "document" in type_lower or "source" in type_lower or "doc" in type_lower:
            return "Source"

        return "entity"

    # 测试用例
    test_cases = [
        # Schema 标准类型
        ("Hospital", "Hospital"),
        ("DepartmentGroup", "DepartmentGroup"),
        ("FunctionalZone", "FunctionalZone"),
        ("Space", "Space"),
        ("DesignMethod", "DesignMethod"),
        ("Source", "Source"),

        # 旧的类型名称（兼容性）
        ("core_entity", "entity"),
        ("related_entity", "entity"),
        ("hospital_concept", "Hospital"),
        ("room_type", "Space"),
        ("design_standard_doc", "Source"),  # 包含 "document"
        ("diagram_atlas_doc", "Source"),    # 包含 "document"

        # 边界情况
        (None, "entity"),
        ("", "entity"),
        ("unknown_type", "entity"),
    ]

    passed = 0
    failed = 0

    for input_type, expected in test_cases:
        result = mapNodeType(input_type)
        if result == expected:
            print(f"[OK] {input_type} -> {result}")
            passed += 1
        else:
            print(f"[FAIL] {input_type} -> {result} (expected: {expected})")
            failed += 1

    print(f"\n测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def test_color_mapping():
    """测试颜色映射"""
    print("\n[TEST] 颜色映射测试")
    print("=" * 60)

    # 模拟 D3 的颜色映射
    typeColors = {
        "Hospital": "#fbbf24",
        "DepartmentGroup": "#3b82f6",
        "FunctionalZone": "#8b5cf6",
        "Space": "#10b981",
        "DesignMethod": "#f59e0b",
        "DesignMethodCategory": "#f97316",
        "Case": "#ec4899",
        "Source": "#14b8a6",
        "MedicalService": "#06b6d4",
        "MedicalEquipment": "#6366f1",
        "TreatmentMethod": "#a855f7",
        "entity": "#94a3b8",
    }

    def getNodeColor(node_type):
        return typeColors.get(node_type, "#94a3b8")

    # 测试所有 schema 类型都有颜色
    schema_types = [
        "Hospital", "DepartmentGroup", "FunctionalZone", "Space",
        "DesignMethod", "DesignMethodCategory", "Case", "Source"
    ]

    all_have_colors = True
    for node_type in schema_types:
        color = getNodeColor(node_type)
        if color == "#94a3b8":  # 默认颜色
            print(f"[FAIL] {node_type} 没有专属颜色")
            all_have_colors = False
        else:
            print(f"[OK] {node_type} -> {color}")

    if all_have_colors:
        print(f"\n[SUCCESS] 所有 schema 类型都有专属颜色")
    else:
        print(f"\n[FAIL] 部分 schema 类型缺少颜色定义")

    return all_have_colors


def test_graph_legend():
    """测试图例配置"""
    print("\n[TEST] 图例配置测试")
    print("=" * 60)

    # 前端图例配置
    legend_items = [
        ("医院", "#fbbf24"),
        ("部门", "#3b82f6"),
        ("功能分区", "#8b5cf6"),
        ("空间", "#10b981"),
        ("设计方法", "#f59e0b"),
        ("资料来源", "#14b8a6"),
    ]

    # 对应的节点类型
    type_mapping = {
        "医院": "Hospital",
        "部门": "DepartmentGroup",
        "功能分区": "FunctionalZone",
        "空间": "Space",
        "设计方法": "DesignMethod",
        "资料来源": "Source",
    }

    # D3 颜色配置
    typeColors = {
        "Hospital": "#fbbf24",
        "DepartmentGroup": "#3b82f6",
        "FunctionalZone": "#8b5cf6",
        "Space": "#10b981",
        "DesignMethod": "#f59e0b",
        "Source": "#14b8a6",
    }

    all_match = True
    for label, legend_color in legend_items:
        node_type = type_mapping[label]
        d3_color = typeColors.get(node_type)

        if legend_color == d3_color:
            print(f"[OK] {label} ({node_type}) -> {legend_color}")
        else:
            print(f"[FAIL] {label} ({node_type}) 颜色不匹配: 图例={legend_color}, D3={d3_color}")
            all_match = False

    if all_match:
        print(f"\n[SUCCESS] 图例和 D3 颜色配置一致")
    else:
        print(f"\n[FAIL] 图例和 D3 颜色配置不一致")

    return all_match


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("知识图谱显示修复 - 类型映射测试")
    print("=" * 60)

    results = []
    results.append(("节点类型映射", test_node_type_mapping()))
    results.append(("颜色映射", test_color_mapping()))
    results.append(("图例配置", test_graph_legend()))

    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} {test_name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n[SUCCESS] 所有测试通过")
    else:
        print("\n[FAIL] 部分测试失败")

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
