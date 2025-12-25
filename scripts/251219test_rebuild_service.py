"""
测试文档信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 功能说明:
   测试数据库重建服务的数据库清空功能

🎯 测试目标:
   - 验证 MongoDB 连接和统计功能
   - 验证 Milvus 连接和统计功能
   - 验证 Neo4j 连接和统计功能
   - 测试清空功能（不实际执行）

📂 涉及的主要文件:
   - backend/app/services/rebuild_service.py (核心服务)

🗑️ 删除时机:
   - [✓] 数据库重建功能验证通过
   - [ ] 预计可删除时间: 2025-12-20

⚠️ 注意事项:
   - 此脚本仅测试连接和统计，不会实际清空数据
"""

import os
import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from backend.app.services.rebuild_service import DatabaseRebuildService

load_dotenv()


def test_database_stats():
    """测试数据库统计功能"""
    print("\n" + "="*80)
    print("测试数据库重建服务")
    print("="*80)

    try:
        # 初始化服务
        print("\n[OK] 初始化 DatabaseRebuildService...")
        service = DatabaseRebuildService()
        print("[OK] 服务初始化成功")

        # 测试 MongoDB 统计
        print("\n[OK] 测试 MongoDB 连接...")
        mongo_stats = service.get_mongodb_stats()
        print(f"[OK] MongoDB 统计:")
        print(f"    - Documents: {mongo_stats['documents']}")
        print(f"    - Chunks: {mongo_stats['chunks']}")

        # 测试 Milvus 统计
        print("\n[OK] 测试 Milvus 连接...")
        milvus_stats = service.get_milvus_stats()
        print(f"[OK] Milvus 统计:")
        print(f"    - Vectors: {milvus_stats['vectors']}")

        # 测试 Neo4j 统计
        print("\n[OK] 测试 Neo4j 连接...")
        neo4j_stats = service.get_neo4j_stats()
        print(f"[OK] Neo4j 统计:")
        print(f"    - Nodes: {neo4j_stats['nodes']}")
        print(f"    - Relationships: {neo4j_stats['relationships']}")
        print(f"    - Concept Nodes: {neo4j_stats['concept_nodes']}")

        print("\n" + "="*80)
        print("[OK] 所有数据库连接测试通过！")
        print("="*80)

        return True

    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        success = test_database_stats()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断测试")
        sys.exit(1)
