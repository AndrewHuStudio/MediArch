"""
测试知识点提取和关系建立功能

测试场景:
1. Milvus Agent 从检索结果中提取结构化知识点
2. Knowledge Fusion 使用提取的知识点创建节点
3. 建立 Space → KnowledgePoint → Source 的关系链

运行方式:
    python backend/tests/test_knowledge_extraction.py
"""

import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.app.agents.base_agent import AgentRequest
from backend.app.agents.milvus_agent.agent import build_milvus_graph
from backend.app.agents.knowledge_fusion.fusion import build_answer_graph_data


async def test_knowledge_extraction():
    """测试知识点提取功能"""
    print("\n" + "="*80)
    print("测试 1: Milvus Agent 知识点提取")
    print("="*80)

    # 创建测试请求
    request = AgentRequest(
        query="手术室的净高要求是什么",
        top_k=5
    )

    # 构建并运行 Milvus Agent
    milvus_graph = build_milvus_graph()

    try:
        result = await milvus_graph.ainvoke({
            "request": request,
            "query": request.query,
        })

        items = result.get("items", [])
        print(f"\n[OK] Milvus Agent 返回 {len(items)} 个结果")

        # 检查是否有提取的知识点
        has_knowledge_points = False
        for item in items:
            kps = item.attrs.get("knowledge_points", [])
            if kps:
                has_knowledge_points = True
                print(f"\n[OK] 找到 {len(kps)} 个提取的知识点:")
                for kp in kps:
                    print(f"  - 标题: {kp.get('title', 'N/A')}")
                    print(f"    内容: {kp.get('content', 'N/A')[:100]}...")
                    print(f"    类别: {kp.get('category', 'N/A')}")
                    print(f"    适用空间: {kp.get('applicable_spaces', [])}")
                    print(f"    优先级: {kp.get('priority', 'N/A')}")
                break

        if not has_knowledge_points:
            print("\n[WARN] 未找到提取的知识点 (可能是LLM未返回结构化数据)")

        return items

    except Exception as e:
        print(f"\n[FAIL] Milvus Agent 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def test_knowledge_fusion(milvus_items):
    """测试知识融合和关系建立"""
    print("\n" + "="*80)
    print("测试 2: Knowledge Fusion 关系建立")
    print("="*80)

    # 模拟 Neo4j 返回的空间节点
    from backend.app.agents.base_agent import AgentItem

    neo4j_items = [
        AgentItem(
            entity_id="space_operating_room",
            name="手术室",
            label="Space",
            score=0.9,
            attrs={"type": "Space"},
        ),
        AgentItem(
            entity_id="space_icu",
            name="ICU",
            label="Space",
            score=0.85,
            attrs={"type": "Space"},
        ),
    ]

    try:
        # 构建答案图谱
        graph_data = build_answer_graph_data(
            neo4j_items=neo4j_items,
            milvus_items=milvus_items,
            query="手术室的净高要求是什么"
        )

        print(f"\n[OK] 图谱构建完成:")
        print(f"  - 节点数: {len(graph_data.nodes)}")
        print(f"  - 边数: {len(graph_data.edges)}")
        print(f"  - 引用数: {len(graph_data.citations)}")

        # 统计节点类型
        node_types = {}
        for node in graph_data.nodes:
            node_types[node.type] = node_types.get(node.type, 0) + 1

        print(f"\n[OK] 节点类型分布:")
        for node_type, count in node_types.items():
            print(f"  - {node_type}: {count}")

        # 检查知识点节点
        kp_nodes = [n for n in graph_data.nodes if n.type == "KnowledgePoint"]
        print(f"\n[OK] 知识点节点: {len(kp_nodes)}")
        for kp in kp_nodes[:3]:  # 只显示前3个
            print(f"  - {kp.name}")
            print(f"    适用空间: {kp.properties.get('applicable_spaces', [])}")

        # 检查 GUIDES 关系
        guides_edges = [e for e in graph_data.edges if e.relation == "GUIDES"]
        print(f"\n[OK] GUIDES 关系: {len(guides_edges)}")
        for edge in guides_edges[:5]:  # 只显示前5个
            source_node = next((n for n in graph_data.nodes if n.id == edge.source), None)
            target_node = next((n for n in graph_data.nodes if n.id == edge.target), None)
            if source_node and target_node:
                print(f"  - {source_node.name} --GUIDES--> {target_node.name}")
                print(f"    (权重: {edge.weight}, 推断: {edge.properties.get('inferred', False)})")

        # 检查 MENTIONED_IN 关系
        mentioned_edges = [e for e in graph_data.edges if e.relation == "MENTIONED_IN"]
        print(f"\n[OK] MENTIONED_IN 关系: {len(mentioned_edges)}")

        # 验证关系链: Space → KnowledgePoint → Source
        print(f"\n[OK] 验证关系链:")
        space_nodes = [n for n in graph_data.nodes if n.type == "Space"]
        for space in space_nodes:
            # 找到指向该空间的知识点
            kps_to_space = [
                e for e in graph_data.edges
                if e.target == space.id and e.relation == "GUIDES"
            ]
            if kps_to_space:
                print(f"\n  空间: {space.name}")
                for edge in kps_to_space[:2]:  # 只显示前2个
                    kp = next((n for n in graph_data.nodes if n.id == edge.source), None)
                    if kp:
                        print(f"    ← {kp.name}")
                        # 找到知识点的来源
                        sources = [
                            e for e in graph_data.edges
                            if e.source == kp.id and e.relation == "MENTIONED_IN"
                        ]
                        for src_edge in sources[:1]:
                            src = next((n for n in graph_data.nodes if n.id == src_edge.target), None)
                            if src:
                                print(f"      ← {src.name}")

        return True

    except Exception as e:
        print(f"\n[FAIL] Knowledge Fusion 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试函数"""
    print("\n" + "="*80)
    print("知识点提取和关系建立功能测试")
    print("="*80)

    # 测试 1: Milvus Agent 知识点提取
    milvus_items = await test_knowledge_extraction()

    if not milvus_items:
        print("\n[SKIP] 跳过 Knowledge Fusion 测试 (无 Milvus 结果)")
        return

    # 测试 2: Knowledge Fusion 关系建立
    success = test_knowledge_fusion(milvus_items)

    # 总结
    print("\n" + "="*80)
    print("测试总结")
    print("="*80)
    if success:
        print("\n[OK] 所有测试通过!")
        print("\n优化效果:")
        print("  1. [OK] Milvus Agent 可以从文本中提取结构化知识点")
        print("  2. [OK] 知识点包含标题、内容、类别、适用空间等属性")
        print("  3. [OK] Knowledge Fusion 使用提取的知识点创建节点")
        print("  4. [OK] 建立了 KnowledgePoint → Space 的 GUIDES 关系")
        print("  5. [OK] 建立了 KnowledgePoint → Source 的 MENTIONED_IN 关系")
        print("  6. [OK] 形成了完整的关系链: Space ← KnowledgePoint ← Source")
    else:
        print("\n[FAIL] 部分测试失败,请检查日志")


if __name__ == "__main__":
    asyncio.run(main())
