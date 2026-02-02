"""
清除知识图谱构建数据，但保留预注入的骨架节点
同时清除 MongoDB 的处理标记，以便从头开始构建

运行方式:
    python backend/cli/clear_kg_keep_skeleton.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase
from pymongo import MongoClient

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parents[2]
sys.path.append(str(project_root))

load_dotenv()


def clear_kg_keep_skeleton():
    """清除构建的实体和关系，保留骨架节点，并清除 MongoDB 处理标记"""

    # Neo4j 配置
    neo4j_uri = os.getenv("NEO4J_URI")
    neo4j_username = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    neo4j_database = os.getenv("NEO4J_DATABASE", "neo4j")

    # MongoDB 配置
    mongodb_uri = os.getenv("MONGODB_URI")
    mongodb_database = os.getenv("MONGODB_DATABASE", "mediarch")

    print("="*80)
    print("清除知识图谱构建数据（保留骨架节点）")
    print("="*80)

    try:
        # ========== 第一步：清除 Neo4j 数据（保留骨架） ==========
        print("\n[1/2] 清除 Neo4j 数据...")
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))

        with driver.session(database=neo4j_database) as session:
            # 统计当前状态
            total_nodes = session.run("MATCH (n) RETURN count(n) as count").single()["count"]
            total_rels = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]

            # 统计骨架节点（有 seed_source 或 is_concept 标记）
            skeleton_nodes = session.run("""
                MATCH (n)
                WHERE n.seed_source IS NOT NULL OR n.is_concept = true
                RETURN count(n) as count
            """).single()["count"]

            print(f"\n当前 Neo4j 状态:")
            print(f"  总节点: {total_nodes}")
            print(f"  总关系: {total_rels}")
            print(f"  骨架节点: {skeleton_nodes}")
            print(f"  构建节点: {total_nodes - skeleton_nodes}")

            if total_nodes == skeleton_nodes:
                print("\n  [INFO] 没有构建数据，只有骨架节点，无需清理 Neo4j")
            else:
                # 确认操作
                print(f"\n将要删除 (Neo4j):")
                print(f"  - 构建的实体节点: {total_nodes - skeleton_nodes} 个")
                print(f"  - 所有关系: {total_rels} 个")
                print(f"\n将要保留 (Neo4j):")
                print(f"  - 骨架节点: {skeleton_nodes} 个")

        driver.close()

        # ========== 第二步：检查 MongoDB 处理标记 ==========
        print("\n[2/2] 检查 MongoDB 处理标记...")
        mongo_client = MongoClient(mongodb_uri)
        db = mongo_client[mongodb_database]
        chunks_collection = db.mediarch_chunks

        # 统计已处理的 chunks
        total_chunks = chunks_collection.count_documents({})
        processed_chunks = chunks_collection.count_documents({"kg_processed": True})

        print(f"\n当前 MongoDB 状态:")
        print(f"  总 chunks: {total_chunks}")
        print(f"  已处理标记: {processed_chunks}")
        print(f"  未处理: {total_chunks - processed_chunks}")

        if processed_chunks > 0:
            print(f"\n将要清除 (MongoDB):")
            print(f"  - 清除 {processed_chunks} 个 chunks 的处理标记")
        else:
            print("\n  [INFO] 没有处理标记，无需清理 MongoDB")

        # ========== 确认操作 ==========
        if total_nodes == skeleton_nodes and processed_chunks == 0:
            print("\n[INFO] 没有需要清理的数据")
            mongo_client.close()
            return

        print("\n" + "="*80)
        confirm = input("确认清除以上数据？(yes/no): ").strip().lower()

        if confirm not in ['yes', 'y']:
            print("[INFO] 已取消操作")
            mongo_client.close()
            return

        # ========== 执行清除 ==========
        print("\n开始清除...")

        # 清除 Neo4j
        if total_nodes > skeleton_nodes:
            driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
            with driver.session(database=neo4j_database) as session:
                # 1. 删除所有关系
                print("  [Neo4j 1/2] 删除所有关系...")
                result = session.run("MATCH ()-[r]->() DELETE r RETURN count(r) as count")
                deleted_rels = result.single()["count"]
                print(f"    [OK] 已删除 {deleted_rels} 个关系")

                # 2. 删除非骨架节点
                print("  [Neo4j 2/2] 删除构建的实体节点...")
                result = session.run("""
                    MATCH (n)
                    WHERE n.seed_source IS NULL AND (n.is_concept IS NULL OR n.is_concept = false)
                    DELETE n
                    RETURN count(n) as count
                """)
                deleted_nodes = result.single()["count"]
                print(f"    [OK] 已删除 {deleted_nodes} 个节点")

                # 验证结果
                remaining_nodes = session.run("MATCH (n) RETURN count(n) as count").single()["count"]
                remaining_rels = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]

                print(f"\n  Neo4j 清除完成:")
                print(f"    剩余节点: {remaining_nodes} (骨架节点)")
                print(f"    剩余关系: {remaining_rels}")

                if remaining_nodes != skeleton_nodes:
                    print(f"    [WARN] 剩余节点数 ({remaining_nodes}) 与骨架节点数 ({skeleton_nodes}) 不一致")

            driver.close()

        # 清除 MongoDB 处理标记
        if processed_chunks > 0:
            print("\n  [MongoDB] 清除处理标记...")
            result = chunks_collection.update_many(
                {"kg_processed": True},
                {"$unset": {"kg_processed": "", "kg_processed_at": ""}}
            )
            print(f"    [OK] 已清除 {result.modified_count} 个 chunks 的处理标记")

            # 验证结果
            remaining_processed = chunks_collection.count_documents({"kg_processed": True})
            print(f"\n  MongoDB 清除完成:")
            print(f"    剩余处理标记: {remaining_processed} (应为 0)")

        mongo_client.close()

        print("\n" + "="*80)
        print("[SUCCESS] 清除完成！")
        print("\n下一步:")
        print("  python backend/databases/graph/build_kg_with_deepseek.py")
        print("  选择 1 (Incremental) 即可从头开始构建")
        print("="*80 + "\n")

    except Exception as e:
        print(f"\n[ERROR] 操作失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    clear_kg_keep_skeleton()
