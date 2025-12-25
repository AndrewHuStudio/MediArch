"""
测试文档信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 功能说明:
   清空 Milvus 向量库并执行全量重新索引

🎯 测试目标:
   - 清空 Milvus collection (mediarch_chunks)
   - 执行文档全量重新索引
   - MongoDB 数据由 FORCE_REINGEST 自动处理

📂 涉及的主要文件:
   - backend/databases/ingestion/indexing/milvus_writer.py (Milvus 操作)
   - backend/databases/ingestion/indexing/pipeline.py (索引 Pipeline)
   - scripts/reindex_documents.py (重新索引脚本)

🗑️ 删除时机:
   - [✓] 全量重新索引完成且验证通过
   - [✓] 知识图谱重建完成
   - [ ] 预计可删除时间: 2025-12-20

⚠️ 注意事项:
   - 此脚本会清空 Milvus 向量库，请提前备份
   - MongoDB 数据由 FORCE_REINGEST 自动清理，无需手动删除
"""

import os
import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BACKEND_ROOT = PROJECT_ROOT / "backend"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from pymilvus import connections, utility, Collection
from dotenv import load_dotenv

# 加载环境变量
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

def clear_milvus():
    """清空 Milvus 向量库"""
    print("\n" + "="*80)
    print("步骤 1: 清空 Milvus 向量库")
    print("="*80)

    try:
        # 连接 Milvus
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")

        print(f"[OK] 连接到 Milvus: {milvus_host}:{milvus_port}")
        connections.connect("default", host=milvus_host, port=milvus_port)

        # 要清理的 collections
        collections_to_clear = ["mediarch_chunks", "entity_attributes"]

        print("\n[OK] 检测到的 collections:")
        all_collections = utility.list_collections()
        for col_name in all_collections:
            print(f"  - {col_name}")

        # 统计当前数据量
        total_vectors = 0
        for collection_name in collections_to_clear:
            if utility.has_collection(collection_name):
                collection = Collection(collection_name)
                collection.load()
                count = collection.num_entities
                total_vectors += count
                print(f"\n[OK] {collection_name}: {count} 个向量")
                collection.release()

        if total_vectors == 0:
            print("\n[OK] 向量库已经是空的，无需清空")
            return True

        # 确认删除
        print(f"\n[WARN] 即将删除 {len([c for c in collections_to_clear if utility.has_collection(c)])} 个 collections，共 {total_vectors} 个向量！")
        print("[INFO] 包括:")
        print("  - mediarch_chunks (文档 chunks 向量)")
        print("  - entity_attributes (实体属性向量，根据混合检索架构已废弃)")
        confirm = input("\n请输入 'YES' 确认删除: ")

        if confirm.strip() == "YES":
            # 删除所有 collections
            deleted_count = 0
            for collection_name in collections_to_clear:
                if utility.has_collection(collection_name):
                    collection = Collection(collection_name)
                    collection.release()
                    collection.drop()
                    print(f"[OK] 已删除 Collection '{collection_name}'")
                    deleted_count += 1
                else:
                    print(f"[SKIP] Collection '{collection_name}' 不存在")

            print(f"\n[OK] Milvus 向量库已清空 (删除了 {deleted_count} 个 collections)")
            return True
        else:
            print("[SKIP] 用户取消操作")
            return False

    except Exception as e:
        print(f"[FAIL] 清空 Milvus 失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def reindex_documents():
    """执行全量重新索引"""
    print("\n" + "="*80)
    print("步骤 2: 执行文档全量重新索引")
    print("="*80)

    try:
        # 导入重新索引脚本的 main 函数
        from scripts.reindex_documents import main as reindex_main

        print("[OK] 开始重新索引...")
        print("[OK] FORCE_REINGEST 已启用，MongoDB 旧数据将自动清理")
        print("")

        reindex_main()

        print("\n[OK] 重新索引完成！")
        return True

    except Exception as e:
        print(f"\n[FAIL] 重新索引失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_results():
    """验证索引结果"""
    print("\n" + "="*80)
    print("步骤 3: 验证索引结果")
    print("="*80)

    try:
        from pymongo import MongoClient

        # 检查 MongoDB
        client = MongoClient(os.getenv('MONGODB_URI'))
        db = client[os.getenv('MONGODB_DATABASE', 'mediarch')]

        doc_count = db['mediarch_documents'].count_documents({})
        chunk_count = db['mediarch_chunks'].count_documents({})

        print(f"\n[MongoDB]")
        print(f"  - Documents: {doc_count}")
        print(f"  - Chunks: {chunk_count}")

        # 检查 Milvus
        connections.connect("default",
                          host=os.getenv("MILVUS_HOST", "localhost"),
                          port=os.getenv("MILVUS_PORT", "19530"))

        if utility.has_collection("mediarch_chunks"):
            collection = Collection("mediarch_chunks")
            collection.load()
            vector_count = collection.num_entities
            print(f"\n[Milvus]")
            print(f"  - Vectors: {vector_count}")

            # 验证一致性
            if chunk_count > 0 and vector_count > 0:
                ratio = vector_count / chunk_count * 100
                print(f"\n[OK] 向量化覆盖率: {ratio:.1f}%")

                if ratio >= 80:
                    print("[OK] 索引质量良好")
                else:
                    print("[WARN] 向量化覆盖率偏低，可能存在问题")
        else:
            print(f"\n[WARN] Milvus collection 不存在")

    except Exception as e:
        print(f"[WARN] 验证失败: {e}")

def main():
    print("="*80)
    print("MediArch 系统：清空并重新索引")
    print("="*80)
    print("\n此脚本将执行以下操作：")
    print("1. 清空 Milvus 向量库")
    print("2. 执行文档全量重新索引 (MongoDB 数据将自动清理)")
    print("3. 验证索引结果")
    print("\n[WARN] 请确保已备份重要数据！")

    confirm = input("\n是否继续？(y/n): ")
    if confirm.lower() != 'y':
        print("\n[SKIP] 用户取消操作")
        return

    # 步骤 1: 清空 Milvus
    if not clear_milvus():
        print("\n[FAIL] 清空 Milvus 失败，终止操作")
        return

    # 步骤 2: 重新索引
    if not reindex_documents():
        print("\n[FAIL] 重新索引失败")
        return

    # 步骤 3: 验证结果
    verify_results()

    print("\n" + "="*80)
    print("[OK] 全量重新索引完成！")
    print("="*80)
    print("\n下一步:")
    print("1. 检查 chunks 质量和完整性")
    print("2. 清空 Neo4j 知识图谱")
    print("3. 重新构建知识图谱")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FAIL] 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
