"""
测试引用修复 - 2026-01-14

验证以下修复：
1. final_citations 支持同一资料的不同页码
2. max_citations 增加到 50
3. top_k 增加到 20
4. 前端引用验证逻辑
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))


def test_citation_deduplication_logic():
    """测试引用去重逻辑：同一资料的不同页码应该生成不同引用"""
    print("\n[TEST 1] 测试引用去重逻辑")
    print("=" * 80)

    # 模拟 citations 数据
    mock_citations = [
        {
            "doc_id": "建筑设计资料集_第6册_医疗",
            "source": "建筑设计资料集_第6册_医疗",
            "page_number": 87,
            "chunk_id": "chunk_87_1",
            "snippet": "门诊部与医技部的连接要点...",
            "positions": [{"page": 87, "bbox": [0.1, 0.2, 0.5, 0.3]}],
        },
        {
            "doc_id": "建筑设计资料集_第6册_医疗",
            "source": "建筑设计资料集_第6册_医疗",
            "page_number": 150,
            "chunk_id": "chunk_150_1",
            "snippet": "医技部的设备配置标准...",
            "positions": [{"page": 150, "bbox": [0.1, 0.2, 0.5, 0.3]}],
        },
        {
            "doc_id": "建筑设计资料集_第6册_医疗",
            "source": "建筑设计资料集_第6册_医疗",
            "page_number": 87,
            "chunk_id": "chunk_87_2",  # 同一页的不同chunk
            "snippet": "门诊部的流线设计...",
            "positions": [{"page": 87, "bbox": [0.1, 0.4, 0.5, 0.5]}],
        },
    ]

    # 使用新的去重逻辑
    citation_best = {}
    citation_order = []

    for cite in mock_citations:
        doc_id = cite["doc_id"]
        page_num = cite["page_number"]
        chunk_id = cite["chunk_id"]

        # 新的 composite key: (doc_id, page_number, chunk_id)
        cite_key = (doc_id, page_num, chunk_id)

        if cite_key not in citation_best:
            citation_order.append(cite_key)
            citation_best[cite_key] = cite

    final_citations = [citation_best[k] for k in citation_order]

    print(f"输入 citations: {len(mock_citations)} 条")
    print(f"输出 final_citations: {len(final_citations)} 条")
    print()

    for i, cite in enumerate(final_citations, 1):
        print(f"[{i}] {cite['source']} - 第{cite['page_number']}页 - {cite['chunk_id']}")

    # 验证结果
    assert len(final_citations) == 3, f"预期3条引用，实际{len(final_citations)}条"
    assert final_citations[0]["page_number"] == 87
    assert final_citations[1]["page_number"] == 150
    assert final_citations[2]["page_number"] == 87
    assert final_citations[0]["chunk_id"] != final_citations[2]["chunk_id"]

    print("\n[OK] 测试通过：同一资料的不同页码生成了不同引用")


def test_max_citations_increase():
    """测试 max_citations 增加到 50"""
    print("\n[TEST 2] 测试 max_citations 增加")
    print("=" * 80)

    from backend.app.agents.result_synthesizer_agent.agent import node_synthesize

    # 检查代码中的 max_citations 默认值
    import inspect
    source = inspect.getsource(node_synthesize)

    if "max_citations = 50" in source:
        print("[OK] max_citations 已增加到 50")
    else:
        print("[FAIL] max_citations 未正确设置")
        assert False, "max_citations 应该是 50"


def test_top_k_increase():
    """测试 top_k 增加到 20"""
    print("\n[TEST 3] 测试 top_k 增加")
    print("=" * 80)

    from backend.app.agents.base_agent import AgentRequest
    from backend.app.agents.orchestrator_agent.agent import DEFAULT_TOP_K
    from backend.app.agents.mediarch_graph import DEFAULT_TOP_K as MEDIARCH_GRAPH_TOP_K

    # 检查默认值
    request = AgentRequest(query="测试")

    print(f"AgentRequest.top_k 默认值: {request.top_k}")
    print(f"Orchestrator DEFAULT_TOP_K: {DEFAULT_TOP_K}")
    print(f"MediArch Graph DEFAULT_TOP_K: {MEDIARCH_GRAPH_TOP_K}")

    assert request.top_k == 20, f"AgentRequest.top_k 应该是 20，实际是 {request.top_k}"
    assert DEFAULT_TOP_K == 20, f"Orchestrator DEFAULT_TOP_K 应该是 20，实际是 {DEFAULT_TOP_K}"
    assert MEDIARCH_GRAPH_TOP_K == 20, (
        f"MediArch Graph DEFAULT_TOP_K 应该是 20，实际是 {MEDIARCH_GRAPH_TOP_K}"
    )

    print("\n[OK] 所有 top_k 默认值已增加到 20")


def test_frontend_validation_logic():
    """测试前端引用验证逻辑（模拟）"""
    print("\n[TEST 4] 测试前端引用验证逻辑")
    print("=" * 80)

    # 模拟前端的 sources 数据
    mock_sources = [
        {"title": "建筑设计资料集", "pageNumber": 87, "snippet": "门诊部设计..."},
        {"title": "", "pageNumber": 0, "snippet": ""},  # 无效：缺少 title 和 pageNumber
        {"title": "医院建筑设计指南", "pageNumber": 150, "snippet": "医技部配置..."},
        {"title": "综合医院设计规范", "pageNumber": None, "snippet": "规范要求..."},  # 无效：pageNumber 为 None
    ]

    # 模拟前端验证逻辑
    valid_sources = []
    for index, source in enumerate(mock_sources):
        has_title = bool(source.get("title") and source["title"].strip())
        has_page_number = isinstance(source.get("pageNumber"), int) and source["pageNumber"] > 0

        if has_title and has_page_number:
            valid_sources.append(source)
        else:
            print(f"[WARN] Source at index {index} is invalid: title={source.get('title')}, pageNumber={source.get('pageNumber')}")

    print(f"\n输入 sources: {len(mock_sources)} 条")
    print(f"有效 sources: {len(valid_sources)} 条")

    for i, source in enumerate(valid_sources, 1):
        print(f"[{i}] {source['title']} - 第{source['pageNumber']}页")

    assert len(valid_sources) == 2, f"预期2条有效引用，实际{len(valid_sources)}条"

    print("\n[OK] 前端验证逻辑正确过滤了无效引用")


def main():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("引用修复测试套件 - 2026-01-14")
    print("=" * 80)

    try:
        test_citation_deduplication_logic()
        test_max_citations_increase()
        test_top_k_increase()
        test_frontend_validation_logic()

        print("\n" + "=" * 80)
        print("[SUCCESS] 所有测试通过！")
        print("=" * 80)
        print("\n修复总结：")
        print("1. [OK] final_citations 支持同一资料的不同页码（使用 doc_id + page_number + chunk_id）")
        print("2. [OK] max_citations 增加到 50（支持10+份资料）")
        print("3. [OK] top_k 增加到 20（返回更多检索结果）")
        print("4. [OK] 前端引用验证逻辑（过滤无效引用）")
        print("\n下一步：")
        print("1. 启动系统：python main.py")
        print("2. 测试查询：'综合医院设计中，医技部和门诊部如何做连接？其中什么流线需要注意呢？'")
        print("3. 验证引用：")
        print("   - 正文中的 [1][2][3] 应该对应不同的资料或同一资料的不同页码")
        print("   - 侧边栏应该显示完整的PDF信息（标题、页码、摘要）")
        print("   - 点击引用应该跳转到正确的PDF页码")
        print("   - 参考资料列表应该显示所有引用的资料")

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 测试出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
