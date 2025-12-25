# backend/cli/rebuild.py
# -*- coding: utf-8 -*-

"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
数据库全量重建工具
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
功能说明:
   清空并重建所有数据库（MongoDB、Milvus、Neo4j）
   重新处理所有 PDF 文档，生成向量索引和知识图谱

涉及的主要文件:
   - backend/app/services/rebuild_service.py (核心重建服务)
   - backend/databases/ingestion/indexing/pipeline.py (文档处理)
   - backend/databases/graph (知识图谱相关)

使用方法:
   # 方法1: 直接运行模块
   python -m backend.cli.rebuild

   # 方法2: 完整重建（包括清空 Neo4j 概念节点）
   python -m backend.cli.rebuild --clear-all

   # 方法3: 只清空数据库不重建
   python -m backend.cli.rebuild --clear-only

   # 方法4: 跳过 Neo4j 清空
   python -m backend.cli.rebuild --skip-neo4j

参数说明:
   --clear-all          清空所有数据（包括 Neo4j 概念节点）
   --clear-only         仅清空数据库，不重新索引
   --skip-mongodb       跳过 MongoDB 清空
   --skip-milvus        跳过 Milvus 清空
   --skip-neo4j         跳过 Neo4j 清空
   --skip-verify        跳过验证步骤
   --no-confirm         跳过确认提示（危险！）
   --verbose            显示详细日志
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

# 导入核心服务
from backend.app.services.rebuild_service import DatabaseRebuildService

# 加载环境变量
load_dotenv()


def setup_logging(verbose: bool = False):
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                f'rebuild_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
                encoding='utf-8'
            )
        ]
    )


def print_banner():
    """打印启动横幅"""
    print("\n" + "="*80)
    print("MediArch 系统 - 数据库全量重建工具")
    print("="*80)
    print("\n功能说明:")
    print("  1. 清空 MongoDB (documents, mediarch_chunks)")
    print("  2. 清空 Milvus (mediarch_chunks collection)")
    print("  3. 清空 Neo4j (保留预注入的概念节点)")
    print("  4. 重新处理所有 PDF (OCR + 图片提取 + VLM 描述)")
    print("  5. 重建向量索引 (Milvus)")
    print("  6. 验证数据完整性")
    print("\n[WARN] 此操作将删除所有现有数据，请确保已备份！")
    print("="*80 + "\n")


def print_current_stats(service: DatabaseRebuildService):
    """打印当前数据库状态"""
    print("\n当前数据库状态:")
    print("-" * 80)

    # MongoDB
    mongo_stats = service.get_mongodb_stats()
    print(f"[MongoDB]")
    print(f"  - Documents: {mongo_stats['documents']}")
    print(f"  - Chunks: {mongo_stats['chunks']}")

    # Milvus
    milvus_stats = service.get_milvus_stats()
    print(f"\n[Milvus]")
    print(f"  - Vectors: {milvus_stats['vectors']}")

    # Neo4j
    neo4j_stats = service.get_neo4j_stats()
    print(f"\n[Neo4j]")
    print(f"  - Nodes: {neo4j_stats['nodes']}")
    print(f"  - Relationships: {neo4j_stats['relationships']}")
    print(f"  - Concept Nodes: {neo4j_stats['concept_nodes']}")

    print("-" * 80 + "\n")


def confirm_action(message: str = "是否继续?") -> bool:
    """确认操作"""
    while True:
        response = input(f"{message} (yes/no): ").strip().lower()
        if response in ['yes', 'y']:
            return True
        elif response in ['no', 'n']:
            return False
        else:
            print("请输入 'yes' 或 'no'")


def print_results(result: dict):
    """打印重建结果"""
    print("\n" + "="*80)
    print("重建结果")
    print("="*80)

    # 状态
    status = result.get("status", "unknown")
    print(f"\n状态: {status.upper()}")

    if status == "failed":
        print(f"失败步骤: {result.get('step', 'unknown')}")

    # 统计信息
    stats = result.get("stats", {})
    print("\n清空统计:")
    print(f"  - MongoDB Documents 删除: {stats.get('mongodb_docs_deleted', 0)}")
    print(f"  - MongoDB Chunks 删除: {stats.get('mongodb_chunks_deleted', 0)}")
    print(f"  - Milvus Vectors 删除: {stats.get('milvus_vectors_deleted', 0)}")
    print(f"  - Neo4j Nodes 删除: {stats.get('neo4j_nodes_deleted', 0)}")

    print("\n文档处理统计:")
    print(f"  - PDF 总数: {stats.get('pdf_total', 0)}")
    print(f"  - 成功: {stats.get('pdf_success', 0)}")
    print(f"  - 失败: {stats.get('pdf_failed', 0)}")

    # 失败文件列表
    failed_files = stats.get('failed_files', [])
    if failed_files:
        print("\n失败的文件:")
        for filename, error in failed_files:
            print(f"  - {filename}: {error}")

    # 验证结果
    verification = result.get("verification")
    if verification:
        print("\n验证结果:")
        print(f"  [MongoDB] Documents: {verification['mongodb']['documents']}, "
              f"Chunks: {verification['mongodb']['chunks']}")
        print(f"  [Milvus] Vectors: {verification['milvus']['vectors']}")
        print(f"  [Neo4j] Nodes: {verification['neo4j']['nodes']}, "
              f"Relationships: {verification['neo4j']['relationships']}, "
              f"Concept Nodes: {verification['neo4j']['concept_nodes']}")

        warnings = verification.get('warnings', [])
        if warnings:
            print("\n警告:")
            for warning in warnings:
                print(f"  - {warning}")

    # 时间统计
    duration = result.get("duration_seconds", 0)
    print(f"\n总耗时: {duration:.2f} 秒 ({duration/60:.2f} 分钟)")

    print("\n" + "="*80 + "\n")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="数据库全量重建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m backend.cli.rebuild                    # 标准重建流程
  python -m backend.cli.rebuild --clear-all       # 清空所有数据（包括概念节点）
  python -m backend.cli.rebuild --clear-only      # 仅清空，不重建
  python -m backend.cli.rebuild --skip-neo4j      # 跳过 Neo4j 清空
  python -m backend.cli.rebuild --no-confirm      # 跳过确认（危险！）
        """
    )

    parser.add_argument('--clear-all', action='store_true',
                        help='清空所有数据（包括 Neo4j 概念节点）')
    parser.add_argument('--clear-only', action='store_true',
                        help='仅清空数据库，不重新索引')
    parser.add_argument('--skip-mongodb', action='store_true',
                        help='跳过 MongoDB 清空')
    parser.add_argument('--skip-milvus', action='store_true',
                        help='跳过 Milvus 清空')
    parser.add_argument('--skip-neo4j', action='store_true',
                        help='跳过 Neo4j 清空')
    parser.add_argument('--skip-verify', action='store_true',
                        help='跳过验证步骤')
    parser.add_argument('--no-confirm', action='store_true',
                        help='跳过确认提示（危险！）')
    parser.add_argument('--verbose', action='store_true',
                        help='显示详细日志')

    args = parser.parse_args()

    # 配置日志
    setup_logging(args.verbose)

    # 打印横幅
    print_banner()

    # 初始化服务
    try:
        service = DatabaseRebuildService()
    except Exception as e:
        print(f"[FAIL] 初始化服务失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # 显示当前状态
    print_current_stats(service)

    # 确认操作
    if not args.no_confirm:
        if not confirm_action("[WARN] 即将清空所有数据，是否继续?"):
            print("\n[SKIP] 用户取消操作")
            return 0

    # 执行重建
    print("\n开始执行重建...")

    try:
        result = service.execute_full_rebuild(
            clear_mongodb=not args.skip_mongodb,
            clear_milvus=not args.skip_milvus,
            clear_neo4j=not args.skip_neo4j,
            preserve_concepts=not args.clear_all,
            reindex=not args.clear_only,
            verify=not args.skip_verify
        )

        # 打印结果
        print_results(result)

        # 返回状态码
        if result.get("status") == "success":
            print("[OK] 数据库重建完成！")
            print("\n下一步:")
            print("  1. 运行验证脚本: python scripts/verify_databases.py")
            print("  2. 重建知识图谱: python backend/databases/graph/build_kg_with_deepseek.py")
            print("  3. 测试前端功能")
            return 0
        else:
            print("[FAIL] 数据库重建失败")
            return 1

    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断操作")
        return 130
    except Exception as e:
        print(f"\n\n[FAIL] 发生错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
