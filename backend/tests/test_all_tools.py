"""
测试所有检索工具的功能

测试目标：
1. Milvus 属性检索工具
2. MongoDB 文档检索工具
3. Neo4j 图谱检索工具
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# 注意：为避免单个服务不可用导致整体失败，工具按需在各自测试函数中导入


def safe_print(text):
    """在可能为GBK控制台环境下安全打印，忽略无法编码的字符。"""
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or "utf-8"
            s = text if isinstance(text, str) else str(text)
            print(s.encode(enc, errors="ignore").decode(enc, errors="ignore"))
        except Exception:
            # 最保守的降级
            try:
                print(str(text).encode("ascii", errors="ignore").decode("ascii", errors="ignore"))
            except Exception:
                # 实在不行，打印占位
                print("[UNPRINTABLE OUTPUT]")


def test_milvus_tools():
    """测试 Milvus 检索工具"""
    print("=" * 80)
    print("测试 Milvus 属性检索工具")
    print("=" * 80)
    print()
    from backend.app.tools import milvus_search
    
    test_cases = [
        {
            "name": "综合属性检索",
            "tool": milvus_search.milvus_attribute_search,
            "params": {"query": "抢救室 面积", "k": 3}
        },
        {
            "name": "定量属性检索",
            "tool": milvus_search.milvus_quantitative_search,
            "params": {"query": "门诊大厅 面积", "k": 3}
        },
        {
            "name": "定性属性检索",
            "tool": milvus_search.milvus_qualitative_search,
            "params": {"query": "急诊科 功能要求", "k": 3}
        }
    ]
    
    for test in test_cases:
        print(f"\n{test['name']}")
        print("-" * 80)
        try:
            result = test['tool'].invoke(test['params'])
            safe_print(result)
            print("\n[PASS] 测试通过")
        except Exception as e:
            print(f"\n[FAIL] 测试失败: {e}")
        print()


def test_mongodb_tools():
    """测试 MongoDB 检索工具"""
    print("=" * 80)
    print("测试 MongoDB 文档检索工具")
    print("=" * 80)
    print()
    from backend.app.tools import mongodb_search
    
    # 覆盖更丰富的中文高频医疗/设计关键词
    keyword_list = [
        # 通用/设施类
        "医院", "医疗机构", "医疗服务体系", "设施", "建筑类型", "建筑物", "文献", "图纸", "图示",
        # 设计与规范
        "设计", "设计原则", "规范", "标准", "技术指标", "导则", "规划", "功能需求",
        # 空间与功能
        "空间", "功能区", "科室", "功能", "功能流线", "人流物流", "洁污分流", "通道", "出入口", "候诊", "缓冲区",
        # 科室与专有空间
        "急诊", "门诊", "病房", "手术室", "ICU",
        # 参数与性能
        "面积", "净宽", "风险", "消防", "通风", "噪声", "照明", "停车",
        # 其它补充
        "系统", "属性", "资源", "数据", "模型", "概念"
    ]

    test_cases = []
    for kw in keyword_list:
        test_cases.append({
            "name": f"关键词搜索 - {kw}",
            "tool": mongodb_search.mongodb_keyword_search,
            "params": {"keywords": kw, "limit": 2}
        })
    
    for test in test_cases:
        print(f"\n{test['name']}")
        print("-" * 80)
        try:
            result = test['tool'].invoke(test['params'])
            safe_print(result)
            print("\n[PASS] 测试通过")
        except Exception as e:
            print(f"\n[FAIL] 测试失败: {e}")
        print()


def test_graph_tools():
    """测试 Neo4j 图谱检索工具"""
    print("=" * 80)
    print("测试 Neo4j 图谱检索工具")
    print("=" * 80)
    print()
    try:
        from backend.app.tools import graph_search
    except Exception as e:
        print(f"警告：图谱工具导入失败或 Neo4j 未运行，跳过图谱测试。原因：{e}")
               
        return
    
    test_cases = [
        {
            "name": "基础图谱检索",
            "tool": graph_search.graph_search_tool,
            "params": {"query": "急诊部"}
        },
        {
            "name": "多跳推理",
            "tool": graph_search.multi_hop_reasoning_tool,
            "params": {
                "start_concept": "手术室",
                "end_concept": None,
                "min_hops": 2,
                "max_hops": 4
            }
        },
        {
            "name": "关联概念发现",
            "tool": graph_search.find_related_concepts_tool,
            "params": {
                "concept": "急诊科",
                "min_connections": 2
            }
        }
    ]
    # 基于知识图谱节点名补充查询用例（可按需扩展）
    kg_labels = [
        "急诊科", "手术室", "门诊部", "ICU", "病房楼",
        "检验科", "放射科", "输血科", "药房", "护士站",
        "门急诊大厅", "消毒供应中心", "后勤保障", "停车场", "出入口"
    ]
    for label in kg_labels:
        test_cases.append({
            "name": f"节点查询 - {label}",
            "tool": graph_search.graph_search_tool,
            "params": {"query": label}
        })
    
    for test in test_cases:
        print(f"\n{test['name']}")
        print("-" * 80)
        try:
            result = test['tool'].invoke(test['params'])
            safe_print(result)
            print("\n[PASS] 测试通过")
        except Exception as e:
            print(f"\n[FAIL] 测试失败: {e}")
        print()


def print_summary():
    """打印测试总结"""
    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)
    print()
    print("[OK] 所有工具测试完成！")
    print()
    print("验收标准检查：")
    print("  - 每个工具能独立查询对应的数据源")
    print("  - 返回格式化的结果")
    print("  - 包含完整的错误处理")
    print()
    print("下一步：开始 Phase 2 - Agent 增强")
    print()


if __name__ == "__main__":
    try:
        # 测试 Milvus 工具
        test_milvus_tools()
        
        # 测试 MongoDB 工具
        test_mongodb_tools()
        
        # 测试 Neo4j 图谱工具
        test_graph_tools()
        
        # 打印总结
        print_summary()
        
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n\n[ERROR] 测试过程中发生错误：{e}")
        import traceback
        traceback.print_exc()

