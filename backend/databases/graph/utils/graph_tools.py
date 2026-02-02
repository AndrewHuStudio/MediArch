"""
统一的图谱工具入口：分析与增量

用法：
  python backend/databases/graph/utils/graph_tools.py analyze
  python backend/databases/graph/utils/graph_tools.py incremental --new-docs a.pdf,b.pdf
  python backend/databases/graph/utils/graph_tools.py incremental --auto
"""

import argparse
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def cmd_analyze():
    from .kg_analyzer import KGAnalyzer
    analyzer = KGAnalyzer()
    try:
        analyzer.full_analysis()
    finally:
        analyzer.close()


def cmd_incremental(new_docs: str = None, auto: bool = False):
    from .incremental_update import find_new_documents, ingest_new_documents, incremental_kg_build, verify_update

    if auto:
        print("\n🔍 自动检测新文档...")
        discovered = find_new_documents()
        if not discovered:
            print("✓ 没有发现新文档")
            return
        print(f"发现 {len(discovered)} 个新文档：")
        for p in discovered:
            print("  -", p)
        confirm = input("\n确认处理这些文档? (y/n): ")
        if confirm.lower() != 'y':
            print("已取消")
            return
        ingest_new_documents(discovered)
        titles = [Path(p).name for p in discovered]
        incremental_kg_build(titles)
        verify_update()
        return

    if new_docs:
        paths = [p.strip() for p in new_docs.split(',') if p.strip()]
        if not paths:
            print("❌ 未提供有效的新文档路径")
            return
        ingest_new_documents(paths)
        titles = [Path(p).name for p in paths]
        incremental_kg_build(titles)
        verify_update()
        return

    print("❌ 请指定 --new-docs 或 --auto")


def main():
    parser = argparse.ArgumentParser(description="图谱工具（分析/增量）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("analyze", help="运行完整分析")

    p_inc = subparsers.add_parser("incremental", help="增量更新")
    p_inc.add_argument("--new-docs", type=str, help="新文档路径，逗号分隔")
    p_inc.add_argument("--auto", action="store_true", help="自动检测新文档")

    # 新增：build 子命令
    p_build = subparsers.add_parser("build", help="从 Mongo 构建知识图谱")
    p_build.add_argument("--schema", type=str, default="backend/databases/graph/schemas/medical_architecture.json", help="Schema 路径")
    p_build.add_argument("--chunk-collection", type=str, help="MongoDB chunk 集合名，默认为环境变量或 mediarch_chunks")

    args = parser.parse_args()

    if args.command == "analyze":
        cmd_analyze()
    elif args.command == "incremental":
        cmd_incremental(new_docs=args.new_docs, auto=args.auto)
    elif args.command == "build":
        from backend.databases.graph.builders.kg_builder import MedicalKGBuilder
        if args.chunk_collection:
            os.environ["MONGODB_CHUNK_COLLECTION"] = args.chunk_collection
        builder = MedicalKGBuilder(schema_path=args.schema)
        print("[STEP] 从MongoDB读取文档chunks...")
        stats = builder.build_from_mongodb()
        print("[STEP] 写入Neo4j...")
        builder.write_to_databases()
        builder.close()
        print("[OK] Build complete", stats)


if __name__ == "__main__":
    main()


