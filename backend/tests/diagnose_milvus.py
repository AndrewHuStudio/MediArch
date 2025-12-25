"""Milvus 数据状态诊断脚本

用途：检查 Milvus 集合状态、数据量、测试查询
日期：2025-12-01
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.app.services.milvus_search import get_retriever


def diagnose_milvus():
    """诊断 Milvus 数据状态"""
    print("=" * 60)
    print("Milvus 数据状态诊断")
    print("=" * 60)

    try:
        retriever = get_retriever()
        print("\n[OK] Retriever 初始化成功")
    except Exception as e:
        print(f"\n[FAIL] Retriever 初始化失败: {e}")
        return

    # 1. 检查集合基本信息
    print("\n" + "-" * 60)
    print("1. 集合基本信息")
    print("-" * 60)
    try:
        collection = retriever.collection
        print(f"集合名称: {collection.name}")
        print(f"数据总量: {collection.num_entities} 条")

        # 检查schema
        print(f"\n字段列表:")
        for field in collection.schema.fields:
            print(f"  - {field.name} ({field.dtype})")

        if collection.num_entities == 0:
            print("\n[WARNING] 集合为空！需要导入数据。")
            print("运行命令: python backend/databases/scripts/import_milvus.py")
            return
        else:
            print(f"\n[OK] 集合包含 {collection.num_entities} 条数据")
    except Exception as e:
        print(f"[FAIL] 获取集合信息失败: {e}")
        return

    # 2. 测试查询（不同相似度阈值）
    print("\n" + "-" * 60)
    print("2. 测试查询（关键词：手术室）")
    print("-" * 60)

    test_queries = [
        ("手术室", 0.0),
        ("手术室", 0.3),
        ("手术室", 0.5),
        ("门诊部", 0.0),
        ("住院部", 0.0),
    ]

    for query, min_sim in test_queries:
        try:
            results = retriever.search_attributes(
                query=query,
                k=5,
                min_similarity=min_sim
            )
            print(f"  查询='{query}', 阈值={min_sim}: {len(results)} 条结果")

            # 显示前2条结果
            for idx, result in enumerate(results[:2], 1):
                entity_name = result.get("entity_name", "")
                similarity = result.get("similarity", 0.0)
                attr_type = result.get("attribute_type", "")
                print(f"    [{idx}] {entity_name} (相似度={similarity:.3f}, 类型={attr_type})")
        except Exception as e:
            print(f"  查询='{query}', 阈值={min_sim}: [FAIL] {e}")

    # 3. 检查属性类型分布
    print("\n" + "-" * 60)
    print("3. 属性类型分布")
    print("-" * 60)
    try:
        # 尝试查询不同属性类型
        attr_types = ["功能", "尺寸", "位置", "设备", "材料"]
        for attr_type in attr_types:
            results = retriever.search_attributes(
                query="医院",
                k=10,
                attribute_type=attr_type,
                min_similarity=0.0
            )
            if results:
                print(f"  {attr_type}: {len(results)} 条")
    except Exception as e:
        print(f"[WARNING] 属性类型统计失败: {e}")

    # 4. 检查来源文档分布
    print("\n" + "-" * 60)
    print("4. 来源文档分布")
    print("-" * 60)
    try:
        results = retriever.search_attributes(
            query="医院建筑",
            k=50,
            min_similarity=0.0
        )

        doc_count = {}
        for result in results:
            doc = result.get("source_document", "未知")
            doc_count[doc] = doc_count.get(doc, 0) + 1

        for doc, count in sorted(doc_count.items(), key=lambda x: x[1], reverse=True):
            print(f"  {doc}: {count} 条")
    except Exception as e:
        print(f"[WARNING] 来源文档统计失败: {e}")

    # 5. 总结和建议
    print("\n" + "=" * 60)
    print("诊断总结")
    print("=" * 60)

    if collection.num_entities == 0:
        print("[CRITICAL] Milvus 集合为空，需要导入数据")
        print("建议：运行 python backend/databases/scripts/import_milvus.py")
    elif collection.num_entities < 100:
        print(f"[WARNING] 数据量较少 ({collection.num_entities} 条)，可能影响检索效果")
        print("建议：检查数据导入是否完整")
    else:
        print(f"[OK] 数据量正常 ({collection.num_entities} 条)")

    print("\n如果查询返回0条结果，可能原因：")
    print("1. 相似度阈值过高 - 建议使用 min_similarity=0.0 测试")
    print("2. 查询关键词不匹配 - 尝试更通用的关键词如'医院'、'设计'")
    print("3. Embedding 模型不匹配 - 检查写入和检索使用的模型是否一致")


if __name__ == "__main__":
    diagnose_milvus()
