"""
验证 Milvus 向量库和 MongoDB 数据
"""
import os
from pathlib import Path
import sys

# 添加项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

print("="*80)
print("验证数据库状态")
print("="*80)

# 1. 检查 MongoDB
print("\n[1] MongoDB 数据检查...")
try:
    from pymongo import MongoClient

    mongo_uri = os.getenv('MONGODB_URI')
    db_name = os.getenv('MONGODB_DATABASE', 'mediarch')

    client = MongoClient(mongo_uri)
    db = client[db_name]

    # 检查 documents 集合
    docs_count = db.documents.count_documents({})
    print(f"  - documents 集合: {docs_count} 个文档")

    # 检查 mediarch_chunks 集合
    chunks_count = db.mediarch_chunks.count_documents({})
    print(f"  - mediarch_chunks 集合: {chunks_count} 个分块")

    # 检查是否有 embedding
    with_embedding = db.mediarch_chunks.count_documents({'embedding': {'$exists': True}})
    print(f"  - 包含 embedding 的分块: {with_embedding} 个")

    if chunks_count > 0:
        print("\n  [OK] MongoDB 数据已存在")
    else:
        print("\n  [WARN] MongoDB 数据为空")

except Exception as e:
    print(f"  [FAIL] MongoDB 检查失败: {e}")

# 2. 检查 Milvus
print("\n[2] Milvus 向量库检查...")
try:
    from pymilvus import connections, Collection, utility

    milvus_host = os.getenv('MILVUS_HOST', 'localhost')
    milvus_port = os.getenv('MILVUS_PORT', '19530')

    connections.connect(host=milvus_host, port=milvus_port)

    # 检查 collection 是否存在
    collection_name = "mediarch_chunks"
    if utility.has_collection(collection_name):
        collection = Collection(collection_name)
        num_entities = collection.num_entities
        print(f"  - Collection '{collection_name}' 存在")
        print(f"  - 向量数量: {num_entities}")

        if num_entities > 0:
            print("\n  [OK] Milvus 向量库已存在")
        else:
            print("\n  [WARN] Milvus collection 存在但为空")
    else:
        print(f"  [WARN] Collection '{collection_name}' 不存在")

except Exception as e:
    print(f"  [FAIL] Milvus 检查失败: {e}")

# 3. 检查 Neo4j
print("\n[3] Neo4j 知识图谱检查...")
try:
    from neo4j import GraphDatabase

    neo4j_uri = os.getenv('NEO4J_URI', 'bolt://localhost:7687')
    neo4j_user = os.getenv('NEO4J_USER', 'neo4j')
    neo4j_password = os.getenv('NEO4J_PASSWORD', 'mediarch2024')

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

    with driver.session() as session:
        # 统计节点数
        result = session.run("MATCH (n) RETURN count(n) as count")
        node_count = result.single()['count']

        # 统计边数
        result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
        edge_count = result.single()['count']

        print(f"  - 节点数: {node_count}")
        print(f"  - 边数: {edge_count}")

        if node_count > 0:
            print("\n  [OK] Neo4j 知识图谱已存在")
        else:
            print("\n  [WARN] Neo4j 知识图谱为空")

    driver.close()

except Exception as e:
    print(f"  [FAIL] Neo4j 检查失败: {e}")

print("\n" + "="*80)
print("验证完成")
print("="*80)
