"""
重新索引所有文档脚本

[FIX 2025-12-09] 添加 file_path 字段支持 PDF 预览

使用方法:
    cd backend
    python -c "exec(open('databases/ingestion/scripts/reindex_all.py').read())"

或者:
    cd "E:/MyPrograms/250804-MediArch System"
    python backend/databases/ingestion/scripts/reindex_all.py

环境变量:
    FORCE_REINGEST=1  # 强制重新索引已存在的文档
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
current_file = Path(__file__).resolve()
# 从 scripts -> ingestion -> databases -> backend -> project_root
project_root = current_file.parents[4]
backend_root = project_root / "backend"

# 添加到 Python 路径
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

print(f"[DEBUG] Project root: {project_root}")
print(f"[DEBUG] Backend root: {backend_root}")
print(f"[DEBUG] Python path: {sys.path[:3]}")

# 导入模块
try:
    from databases.ingestion.indexing.pipeline import IngestionPipeline
    print("[OK] Successfully imported IngestionPipeline")
except ImportError as e:
    print(f"[FAIL] Import error: {e}")
    print("[INFO] Trying alternative import...")
    # 尝试直接导入
    sys.path.insert(0, str(backend_root / "databases" / "ingestion"))
    from indexing.pipeline import IngestionPipeline
    print("[OK] Successfully imported IngestionPipeline (alternative method)")

from dotenv import load_dotenv

# 加载环境变量
env_path = project_root / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"[OK] Loaded .env from {env_path}")
else:
    load_dotenv()
    print("[WARN] .env not found, using system environment")

def main():
    # 文档目录
    documents_dir = project_root / "backend" / "databases" / "documents"

    # 子目录映射
    categories = {
        "标准规范": "标准规范",
        "参考论文": "参考论文",
        "书籍报告": "书籍报告",
        "政策文件": "政策文件",
    }

    # 初始化 pipeline
    print("[OK] 初始化索引 Pipeline...")
    pipeline = IngestionPipeline(
        engine="mineru",  # 或 "marker"
        max_chunk_size=1200,
        chunk_overlap=100,
    )

    # 设置强制重新索引
    os.environ["FORCE_REINGEST"] = "1"

    total_files = 0
    success_files = 0
    failed_files = []

    # 遍历所有类别
    for category_name, category_dir in categories.items():
        category_path = documents_dir / category_dir

        if not category_path.exists():
            print(f"[SKIP] 目录不存在: {category_path}")
            continue

        print(f"\n[OK] 处理类别: {category_name}")
        print(f"[OK] 目录: {category_path}")

        # 查找所有 PDF 文件
        pdf_files = list(category_path.glob("*.pdf"))
        print(f"[OK] 找到 {len(pdf_files)} 个 PDF 文件")

        for pdf_file in pdf_files:
            total_files += 1
            print(f"\n[{total_files}] 处理: {pdf_file.name}")

            try:
                # 处理文档
                result = pipeline.process_document(
                    pdf_path=str(pdf_file),
                    category=category_name,
                )

                if result.get("status") == "success":
                    success_files += 1
                    print(f"[OK] 成功: {pdf_file.name}")
                    print(f"    - MongoDB Doc ID: {result.get('mongo_doc_id')}")
                    print(f"    - Chunks: {result.get('total_chunks', 0)}")
                else:
                    print(f"[WARN] 状态: {result.get('status')}")
                    print(f"    - 原因: {result.get('reason', 'unknown')}")

            except Exception as e:
                failed_files.append((pdf_file.name, str(e)))
                print(f"[FAIL] 失败: {pdf_file.name}")
                print(f"    - 错误: {e}")

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
            print(f"  - {filename}: {error}")

    print("\n[OK] 重新索引完成！")

if __name__ == "__main__":
    main()
