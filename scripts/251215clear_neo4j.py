"""
测试文档信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 功能说明:
   清空 Neo4j 知识图谱数据库

🎯 测试目标:
   - 删除 Neo4j 中的所有节点和关系
   - 为重新构建知识图谱做准备

📂 涉及的主要文件:
   - backend/databases/graph (知识图谱相关代码)

🗑️ 删除时机:
   - [✓] 知识图谱重建完成且验证通过
   - [ ] 预计可删除时间: 2025-12-20

⚠️ 注意事项:
   - 此脚本会删除所有知识图谱数据
   - 请确保已备份 Neo4j 数据库
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# 加载环境变量
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

def clear_neo4j():
    """清空 Neo4j 知识图谱"""
    print("\n" + "="*80)
    print("清空 Neo4j 知识图谱")
    print("="*80)

    try:
        # 连接 Neo4j
        neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_password = os.getenv("NEO4J_PASSWORD", "mediarch2024")
        neo4j_database = os.getenv("NEO4J_DATABASE", "neo4j")

        print(f"[OK] 连接到 Neo4j: {neo4j_uri}")
        print(f"[OK] 数据库: {neo4j_database}")

        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

        with driver.session(database=neo4j_database) as session:
            # 获取当前节点和关系数量
            result = session.run("MATCH (n) RETURN count(n) as node_count")
            node_count = result.single()["node_count"]

            result = session.run("MATCH ()-[r]->() RETURN count(r) as rel_count")
            rel_count = result.single()["rel_count"]

            print(f"\n[OK] 当前数据统计:")
            print(f"  - 节点数: {node_count}")
            print(f"  - 关系数: {rel_count}")

            if node_count == 0 and rel_count == 0:
                print("\n[OK] 数据库已经是空的，无需清空")
                driver.close()
                return True

            # 确认删除
            print("\n[WARN] 即将删除所有节点和关系！")
            confirm = input("请输入 'YES' 确认删除: ")

            if confirm.strip() == "YES":
                # 删除所有关系
                print("\n[OK] 正在删除关系...")
                result = session.run("MATCH ()-[r]->() DELETE r RETURN count(r) as deleted")
                deleted_rels = result.single()["deleted"]
                print(f"[OK] 已删除 {deleted_rels} 个关系")

                # 删除所有节点
                print("\n[OK] 正在删除节点...")
                result = session.run("MATCH (n) DELETE n RETURN count(n) as deleted")
                deleted_nodes = result.single()["deleted"]
                print(f"[OK] 已删除 {deleted_nodes} 个节点")

                print("\n[OK] Neo4j 知识图谱已清空")
                driver.close()
                return True
            else:
                print("\n[SKIP] 用户取消操作")
                driver.close()
                return False

    except Exception as e:
        print(f"\n[FAIL] 清空 Neo4j 失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("="*80)
    print("MediArch 系统：清空 Neo4j 知识图谱")
    print("="*80)
    print("\n此脚本将删除 Neo4j 中的所有节点和关系")
    print("[WARN] 请确保已备份重要数据！")

    confirm = input("\n是否继续？(y/n): ")
    if confirm.lower() != 'y':
        print("\n[SKIP] 用户取消操作")
        return

    if clear_neo4j():
        print("\n" + "="*80)
        print("[OK] Neo4j 清空完成！")
        print("="*80)
        print("\n下一步:")
        print("1. 运行知识图谱构建脚本")
        print("2. 验证知识图谱质量")
        print("3. 解决重复节点等问题")
    else:
        print("\n[FAIL] 清空失败")

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
