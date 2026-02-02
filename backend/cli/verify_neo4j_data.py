"""
Neo4j 数据完整性验证脚本

验证项：
1. Neo4j 连接状态
2. 节点和关系统计
3. chunk_ids 关联验证
4. 实体类型分布
5. 示例数据查看
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from neo4j import GraphDatabase
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class Neo4jVerifier:
    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")

        print(f"[连接] Neo4j URI: {uri}")
        print(f"[连接] User: {user}")
        print(f"[连接] Database: {self.database}")

        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()
            print("[OK] Neo4j 连接成功\n")
        except Exception as e:
            print(f"[FAIL] Neo4j 连接失败: {e}")
            sys.exit(1)

    def close(self):
        if self.driver:
            self.driver.close()

    def run_query(self, query, params=None):
        """执行查询并返回结果"""
        with self.driver.session(database=self.database) as session:
            result = session.run(query, params or {})
            return [record.data() for record in result]

    def verify_basic_stats(self):
        """验证基础统计数据"""
        print("=" * 60)
        print("1. 基础统计")
        print("=" * 60)

        # 总节点数
        query = "MATCH (n) RETURN count(n) as total_nodes"
        result = self.run_query(query)
        total_nodes = result[0]['total_nodes']
        print(f"[节点] 总数: {total_nodes:,}")

        if total_nodes == 0:
            print("[WARN] Neo4j 中没有任何节点！请先运行 build_kg.py 构建知识图谱")
            return False

        # 总关系数
        query = "MATCH ()-[r]->() RETURN count(r) as total_rels"
        result = self.run_query(query)
        total_rels = result[0]['total_rels']
        print(f"[关系] 总数: {total_rels:,}")

        # 节点标签分布
        query = """
        MATCH (n)
        RETURN labels(n)[0] as label, count(n) as count
        ORDER BY count DESC
        """
        results = self.run_query(query)
        print(f"\n[节点类型分布]")
        for r in results[:10]:
            print(f"  {r['label']:20s}: {r['count']:>6,}")

        # 关系类型分布
        query = """
        MATCH ()-[r]->()
        RETURN type(r) as rel_type, count(r) as count
        ORDER BY count DESC
        LIMIT 10
        """
        results = self.run_query(query)
        print(f"\n[关系类型分布]")
        for r in results:
            print(f"  {r['rel_type']:20s}: {r['count']:>6,}")

        print()
        return True

    def verify_chunk_association(self):
        """验证 chunk_ids 关联"""
        print("=" * 60)
        print("2. chunk_ids 关联验证")
        print("=" * 60)

        # 有 chunk_ids 的节点数
        query = """
        MATCH (n)
        WHERE n.chunk_ids IS NOT NULL AND size(n.chunk_ids) > 0
        RETURN count(n) as nodes_with_chunks
        """
        result = self.run_query(query)
        nodes_with_chunks = result[0]['nodes_with_chunks']
        print(f"[OK] 有 chunk_ids 关联的节点: {nodes_with_chunks:,}")

        # chunk_ids 分布统计
        query = """
        MATCH (n)
        WHERE n.chunk_ids IS NOT NULL
        RETURN size(n.chunk_ids) as chunk_count, count(n) as node_count
        ORDER BY chunk_count DESC
        LIMIT 10
        """
        results = self.run_query(query)
        print(f"\n[chunk_ids 数量分布]")
        for r in results:
            print(f"  {r['chunk_count']} chunks -> {r['node_count']} nodes")

        # 关系的 chunk_ids
        query = """
        MATCH ()-[r]->()
        WHERE r.chunk_ids IS NOT NULL AND size(r.chunk_ids) > 0
        RETURN count(r) as rels_with_chunks
        """
        result = self.run_query(query)
        rels_with_chunks = result[0]['rels_with_chunks']
        print(f"\n[OK] 有 chunk_ids 关联的关系: {rels_with_chunks:,}")

        print()

    def verify_sample_data(self):
        """查看示例数据"""
        print("=" * 60)
        print("3. 示例数据")
        print("=" * 60)

        # 查看一个实体节点
        query = """
        MATCH (n)
        WHERE n.chunk_ids IS NOT NULL AND size(n.chunk_ids) > 0
        RETURN n.id as id, labels(n)[0] as label, n.name as name,
               n.chunk_ids as chunk_ids, size(n.chunk_ids) as chunk_count
        LIMIT 3
        """
        results = self.run_query(query)
        print("[实体节点示例]")
        for i, r in enumerate(results, 1):
            print(f"\n  示例 {i}:")
            print(f"    ID: {r['id']}")
            print(f"    类型: {r['label']}")
            print(f"    名称: {r['name']}")
            print(f"    关联chunks: {r['chunk_count']} 个")
            print(f"    chunk_ids: {r['chunk_ids'][:2]}...")  # 只显示前2个

        # 查看一个关系
        query = """
        MATCH (a)-[r]->(b)
        WHERE r.chunk_ids IS NOT NULL
        RETURN a.name as source, type(r) as rel_type, b.name as target,
               size(r.chunk_ids) as chunk_count
        LIMIT 3
        """
        results = self.run_query(query)
        print(f"\n[关系示例]")
        for i, r in enumerate(results, 1):
            print(f"\n  示例 {i}:")
            print(f"    {r['source']} --[{r['rel_type']}]--> {r['target']}")
            print(f"    关联chunks: {r['chunk_count']} 个")

        print()

    def verify_milvus_association(self):
        """验证 Milvus 关联（如果有）"""
        print("=" * 60)
        print("4. Milvus 向量关联验证（可选）")
        print("=" * 60)

        query = """
        MATCH (n)
        WHERE n.milvus_vector_ids IS NOT NULL AND size(n.milvus_vector_ids) > 0
        RETURN count(n) as nodes_with_milvus
        """
        result = self.run_query(query)
        nodes_with_milvus = result[0]['nodes_with_milvus']

        if nodes_with_milvus > 0:
            print(f"[OK] 有 milvus_vector_ids 关联的节点: {nodes_with_milvus:,}")
        else:
            print("[INFO] 没有 milvus_vector_ids 关联（可能未启用属性向量存储）")

        print()

    def verify_query_capability(self):
        """验证查询能力"""
        print("=" * 60)
        print("5. 查询能力测试")
        print("=" * 60)

        # 测试全文搜索
        query = """
        CALL db.index.fulltext.queryNodes("entity_name_fulltext", "手术室")
        YIELD node, score
        RETURN node.name as name, labels(node)[0] as label, score
        LIMIT 5
        """
        try:
            results = self.run_query(query)
            if results:
                print("[OK] 全文索引可用，搜索'手术室'结果:")
                for r in results:
                    print(f"  {r['name']:20s} ({r['label']}) - score: {r['score']:.2f}")
            else:
                print("[WARN] 全文索引返回空结果")
        except Exception as e:
            print(f"[WARN] 全文索引不可用: {e}")
            print("      运行以下命令创建索引:")
            print("      CREATE FULLTEXT INDEX entity_name_fulltext FOR (n:Entity) ON EACH [n.name]")

        print()

    def run_all_verifications(self):
        """运行所有验证"""
        print("\n")
        print("*" * 60)
        print("*" + " " * 15 + "Neo4j 数据完整性验证" + " " * 16 + "*")
        print("*" * 60)
        print()

        has_data = self.verify_basic_stats()
        if not has_data:
            return

        self.verify_chunk_association()
        self.verify_sample_data()
        self.verify_milvus_association()
        self.verify_query_capability()

        print("=" * 60)
        print("验证完成！")
        print("=" * 60)
        print()


def main():
    verifier = Neo4jVerifier()
    try:
        verifier.run_all_verifications()
    finally:
        verifier.close()


if __name__ == "__main__":
    main()
