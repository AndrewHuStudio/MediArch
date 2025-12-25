"""
并行索引与知识图谱构建方案

核心思想：
1. OCR + 向量化 与 图谱构建 并行执行
2. 使用消息队列（或简单的文件监控）协调任务
3. 图谱构建完成后，自动建立 chunk_id 关联

使用方法:
    python parallel_index_and_kg.py
"""

import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any
import time

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BACKEND_ROOT = PROJECT_ROOT / "backend"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline
from dotenv import load_dotenv

# 加载环境变量
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

print(f"[OK] Project root: {PROJECT_ROOT}")
print(f"[OK] Backend root: {BACKEND_ROOT}")


class ParallelIndexer:
    """并行索引器：同时进行向量索引和知识图谱构建"""

    def __init__(self):
        """初始化"""
        # 设置强制重新索引
        os.environ["FORCE_REINGEST"] = "1"

        # 初始化 Pipeline
        self.pipeline = DocumentIngestionPipeline(engine="mineru")

        # 任务队列（存储已完成向量化的文档）
        self.completed_docs: List[Dict[str, Any]] = []

    def index_document(self, pdf_path: str, category: str) -> Dict[str, Any]:
        """
        索引单个文档（向量化）

        Args:
            pdf_path: PDF 文件路径
            category: 文档类别

        Returns:
            索引结果
        """
        print(f"\n[Task 1] 开始向量索引: {Path(pdf_path).name}")
        start_time = time.time()

        try:
            result = self.pipeline.process_document(
                pdf_path=pdf_path,
                category=category,
            )

            elapsed = time.time() - start_time
            print(f"[Task 1] 向量索引完成: {Path(pdf_path).name} (耗时: {elapsed:.2f}s)")

            return {
                "status": "success",
                "pdf_path": pdf_path,
                "category": category,
                "result": result,
                "elapsed": elapsed,
            }

        except Exception as e:
            print(f"[Task 1] 向量索引失败: {Path(pdf_path).name} - {e}")
            return {
                "status": "failed",
                "pdf_path": pdf_path,
                "category": category,
                "error": str(e),
            }

    def build_knowledge_graph(self, doc_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建知识图谱（从 MongoDB chunks 提取实体和关系）

        Args:
            doc_info: 文档信息（包含 mongo_doc_id）

        Returns:
            图谱构建结果
        """
        if doc_info["status"] != "success":
            return {"status": "skipped", "reason": "indexing failed"}

        mongo_doc_id = doc_info["result"].get("mongo_doc_id")
        pdf_name = Path(doc_info["pdf_path"]).name

        print(f"\n[Task 2] 开始构建知识图谱: {pdf_name}")
        start_time = time.time()

        try:
            # TODO: 调用知识图谱构建器
            # from backend.databases.graph.builders.kg_builder import MedicalKGBuilder
            # kg_builder = MedicalKGBuilder()
            # kg_result = kg_builder.process_document(mongo_doc_id)

            # 模拟图谱构建（实际应调用 kg_builder）
            time.sleep(2)  # 模拟耗时操作

            elapsed = time.time() - start_time
            print(f"[Task 2] 知识图谱构建完成: {pdf_name} (耗时: {elapsed:.2f}s)")

            return {
                "status": "success",
                "mongo_doc_id": mongo_doc_id,
                "pdf_name": pdf_name,
                "elapsed": elapsed,
            }

        except Exception as e:
            print(f"[Task 2] 知识图谱构建失败: {pdf_name} - {e}")
            return {
                "status": "failed",
                "mongo_doc_id": mongo_doc_id,
                "error": str(e),
            }

    def process_documents_parallel(self, pdf_files: List[tuple]) -> Dict[str, Any]:
        """
        并行处理多个文档

        Args:
            pdf_files: [(pdf_path, category), ...]

        Returns:
            处理统计
        """
        print(f"\n{'='*80}")
        print(f"[OK] 开始并行处理 {len(pdf_files)} 个文档")
        print(f"{'='*80}\n")

        total_start = time.time()
        index_results = []
        kg_results = []

        # 使用线程池并行执行
        with ThreadPoolExecutor(max_workers=4) as executor:
            # 提交所有向量索引任务
            index_futures = {
                executor.submit(self.index_document, pdf_path, category): (pdf_path, category)
                for pdf_path, category in pdf_files
            }

            # 收集向量索引结果，并立即提交图谱构建任务
            kg_futures = []
            for future in as_completed(index_futures):
                index_result = future.result()
                index_results.append(index_result)

                # 如果索引成功，立即提交图谱构建任务
                if index_result["status"] == "success":
                    kg_future = executor.submit(self.build_knowledge_graph, index_result)
                    kg_futures.append(kg_future)

            # 等待所有图谱构建任务完成
            for future in as_completed(kg_futures):
                kg_result = future.result()
                kg_results.append(kg_result)

        total_elapsed = time.time() - total_start

        # 统计结果
        index_success = sum(1 for r in index_results if r["status"] == "success")
        kg_success = sum(1 for r in kg_results if r["status"] == "success")

        print(f"\n{'='*80}")
        print(f"[OK] 并行处理完成")
        print(f"{'='*80}")
        print(f"总耗时: {total_elapsed:.2f}s")
        print(f"向量索引: {index_success}/{len(pdf_files)} 成功")
        print(f"知识图谱: {kg_success}/{len(kg_results)} 成功")

        return {
            "total_files": len(pdf_files),
            "index_success": index_success,
            "kg_success": kg_success,
            "total_elapsed": total_elapsed,
            "index_results": index_results,
            "kg_results": kg_results,
        }


def main():
    """主函数"""
    # 文档目录
    documents_dir = BACKEND_ROOT / "databases" / "documents"

    # 子目录映射
    categories = {
        "标准规范": "标准规范",
        "参考论文": "参考论文",
        "书籍报告": "书籍报告",
        "政策文件": "政策文件",
    }

    # 收集所有 PDF 文件
    pdf_files = []
    for category_name, category_dir in categories.items():
        category_path = documents_dir / category_dir
        if category_path.exists():
            for pdf_file in category_path.glob("*.pdf"):
                pdf_files.append((str(pdf_file), category_name))

    if not pdf_files:
        print("[WARN] 未找到任何 PDF 文件")
        return

    print(f"[OK] 找到 {len(pdf_files)} 个 PDF 文件")

    # 创建并行索引器
    indexer = ParallelIndexer()

    # 并行处理
    result = indexer.process_documents_parallel(pdf_files)

    print(f"\n[OK] 全部完成！")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FAIL] 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
