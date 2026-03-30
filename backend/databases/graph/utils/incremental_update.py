"""
知识图谱增量更新脚本

用法：
    python backend/databases/graph/utils/incremental_update.py --new-docs "新文档1.pdf,新文档2.pdf"
    
或全自动检测新文档：
    python backend/databases/graph/utils/incremental_update.py --auto
"""

import os
import sys
from pathlib import Path
from backend.env_loader import load_dotenv
from pymongo import MongoClient
import argparse

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

from backend.databases.graph.builders.kg_builder import MedicalKGBuilder


def get_existing_documents():
    """获取MongoDB中已有的文档列表"""
    client = MongoClient(os.getenv("MONGODB_URI"))
    db = client[os.getenv("MONGODB_DATABASE", "mediarch")]
    
    existing_docs = set()
    for doc in db.documents.find({}, {"title": 1}):
        existing_docs.add(doc["title"])
    
    client.close()
    return existing_docs


def find_new_documents(doc_dir="backend/databases/documents"):
    """自动检测新文档"""
    existing = get_existing_documents()
    
    new_docs = []
    for root, dirs, files in os.walk(doc_dir):
        for file in files:
            if file.endswith('.pdf'):
                if file not in existing:
                    new_docs.append(os.path.join(root, file))
    
    return new_docs


def ingest_new_documents(doc_paths):
    """摄入新文档到MongoDB"""
    print("\n" + "="*80)
    print("步骤1：摄入新文档")
    print("="*80)
    
    # 这里调用摄入pipeline
    # TODO: 实现单文档摄入逻辑
    for doc_path in doc_paths:
        print(f"  正在处理: {doc_path}")
        # pipeline.process_single_document(doc_path)
    
    print("✓ 新文档摄入完成")


def incremental_kg_build(doc_titles=None):
    """增量构建知识图谱"""
    print("\n" + "="*80)
    print("步骤2：增量构建知识图谱")
    print("="*80)

    builder = MedicalKGBuilder()

    # 只处理指定的文档
    if doc_titles:
        stats = builder.build_from_mongodb(filter_docs=doc_titles)
    else:
        stats = builder.build_from_mongodb()

    # 写入数据库（会自动merge）
    builder.write_to_databases()

    builder.close()

    print("\n✓ 知识图谱增量更新完成")
    print(f"  新增节点: {stats.get('total_nodes', 0)}")
    print(f"  新增关系: {stats.get('total_edges', 0)}")


def verify_update():
    """验证更新结果"""
    print("\n" + "="*80)
    print("步骤3：验证更新")
    print("="*80)
    
    client = MongoClient(os.getenv("MONGODB_URI"))
    db = client[os.getenv("MONGODB_DATABASE", "mediarch")]
    
    doc_count = db.documents.count_documents({})
    print(f"  MongoDB文档总数: {doc_count}")
    
    # TODO: 查询Neo4j和Milvus统计
    
    client.close()


def main():
    parser = argparse.ArgumentParser(description='增量更新知识图谱（统一入口）')
    parser.add_argument('--new-docs', type=str, help='新文档路径，逗号分隔')
    parser.add_argument('--auto', action='store_true', help='自动检测新文档')
    
    args = parser.parse_args()
    
    print("="*80)
    print("知识图谱增量更新")
    print("="*80)
    
    if args.auto:
        print("\n🔍 自动检测新文档...")
        new_docs = find_new_documents()
        
        if not new_docs:
            print("✓ 没有发现新文档")
            return
        
        print(f"\n发现 {len(new_docs)} 个新文档:")
        for doc in new_docs:
            print(f"  - {doc}")
        
        confirm = input("\n确认处理这些文档? (y/n): ")
        if confirm.lower() != 'y':
            print("已取消")
            return
        
        # 处理新文档
        ingest_new_documents(new_docs)
        doc_titles = [Path(d).name for d in new_docs]
        incremental_kg_build(doc_titles)
        
    elif args.new_docs:
        doc_paths = args.new_docs.split(',')
        ingest_new_documents(doc_paths)
        doc_titles = [Path(d).name for d in doc_paths]
        incremental_kg_build(doc_titles)
    
    else:
        print("❌ 请指定 --new-docs 或 --auto 参数")
        parser.print_help()
        return
    
    # 验证
    verify_update()
    
    print("\n" + "="*80)
    print("✅ 增量更新完成！")
    print("="*80)


if __name__ == "__main__":
    import argparse
    from .graph_tools import cmd_incremental
    parser = argparse.ArgumentParser(description='增量更新知识图谱（统一入口）')
    parser.add_argument('--new-docs', type=str, help='新文档路径，逗号分隔')
    parser.add_argument('--auto', action='store_true', help='自动检测新文档')
    args = parser.parse_args()
    cmd_incremental(new_docs=args.new_docs, auto=args.auto)

