"""
重新索引所有文档脚本 - 简化版

[FIX 2025-12-09] 添加 file_path 字段支持 PDF 预览

使用方法:
    cd "E:\MyPrograms\250804-MediArch System"
    python reindex_documents.py
"""

import os
import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()  # scripts/ -> 项目根目录
BACKEND_ROOT = PROJECT_ROOT / "backend"

# 添加到 Python 路径
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

print(f"[OK] Project root: {PROJECT_ROOT}")
print(f"[OK] Backend root: {BACKEND_ROOT}")

# 导入必要的模块
from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline
from dotenv import load_dotenv

# 加载环境变量
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)
print(f"[OK] Loaded environment from {env_path}")

def main():
    # 文档目录
    documents_dir = BACKEND_ROOT / "databases" / "documents"

    # 子目录映射
    categories = {
        "标准规范": "标准规范",
        "参考论文": "参考论文",
        "书籍报告": "书籍报告",
        "政策文件": "政策文件",
    }

    # 设置强制重新索引（必须在初始化 Pipeline 之前）
    os.environ["FORCE_REINGEST"] = "1"

    # 初始化 pipeline
    print("\n[OK] 初始化索引 Pipeline...")
    pipeline = DocumentIngestionPipeline(
        engine="mineru",  # 或 "marker"
    )

    total_files = 0
    success_files = 0
    failed_files = []

    # 遍历所有类别
    for category_name, category_dir in categories.items():
        category_path = documents_dir / category_dir

        if not category_path.exists():
            print(f"\n[SKIP] 目录不存在: {category_path}")
            continue

        print(f"\n{'='*80}")
        print(f"[OK] 处理类别: {category_name}")
        print(f"[OK] 目录: {category_path}")
        print(f"{'='*80}")

        # 查找所有 PDF 文件
        pdf_files = list(category_path.glob("*.pdf"))
        print(f"[OK] 找到 {len(pdf_files)} 个 PDF 文件\n")

        for pdf_file in pdf_files:
            total_files += 1
            print(f"[{total_files}] 处理: {pdf_file.name}")

            try:
                # 处理文档
                result = pipeline.process_document(
                    pdf_path=str(pdf_file),
                    category=category_name,
                )

                if result.get("status") == "success":
                    success_files += 1
                    print(f"    [OK] 成功")
                    print(f"    - MongoDB Doc ID: {result.get('mongo_doc_id')}")
                    print(f"    - Chunks: {result.get('total_chunks', 0)}")
                else:
                    print(f"    [WARN] 状态: {result.get('status')}")
                    print(f"    - 原因: {result.get('reason', 'unknown')}")

            except Exception as e:
                failed_files.append((pdf_file.name, str(e)))
                print(f"    [FAIL] 失败")
                print(f"    - 错误: {e}")

            print()  # 空行分隔

    # 打印总结
    print("\n" + "="*80)
    print("索引完成总结")
    print("="*80)
    print(f"总文件数: {total_files}")
    print(f"成功: {success_files}")
    print(f"失败: {len(failed_files)}")

    if failed_files:
        print("\n失败的文件:")
        for filename, error in failed_files:
            print(f"  - {filename}")
            print(f"    错误: {error[:100]}...")

    print("\n[OK] 重新索引完成！")

    # 验证结果
    print("\n" + "="*80)
    print("验证 file_path 字段")
    print("="*80)

    try:
        from pymongo import MongoClient

        client = MongoClient(os.getenv('MONGODB_URI'))
        db = client[os.getenv('MONGODB_DATABASE', 'mediarch')]
        collection = db['mediarch_chunks']

        total = collection.count_documents({})
        with_path = collection.count_documents({'file_path': {'$exists': True, '$ne': None}})

        print(f"Total documents: {total}")
        print(f"Documents with file_path: {with_path}")
        print(f"Coverage: {with_path/total*100:.1f}%")

        if with_path == total:
            print("\n[OK] All documents have file_path!")
        else:
            print(f"\n[WARN] {total - with_path} documents missing file_path")

    except Exception as e:
        print(f"[WARN] 无法验证: {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断，已停止索引")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FAIL] 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
