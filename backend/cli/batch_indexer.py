"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
批量文档索引器
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
功能说明:
   批量处理所有文档，进行 OCR、分块、向量化并写入数据库

涉及的主要文件:
   - backend/databases/ingestion/indexing/pipeline.py (核心流程)
   - backend/databases/ingestion/indexing/embedding.py (向量化)
   - backend/databases/ingestion/indexing/milvus_writer.py (向量存储)
   - backend/databases/ingestion/indexing/mongodb_writer.py (文档存储)

使用方法:
   # 方法1: 直接运行模块
   python -m backend.cli.batch_indexer

   # 方法2: 使用参数
   python -m backend.cli.batch_indexer --force --category 标准规范

   # 方法3: 从项目根目录运行
   cd "E:\MyPrograms\250804-MediArch System"
   python -m backend.cli.batch_indexer

参数说明:
   --force              强制重新索引（忽略已存在的文档）
   --category <名称>    仅处理指定类别
   --engine <引擎>      指定 OCR 引擎（mineru/marker）
   --verbose            显示详细日志
   --skip-validation    跳过最后的验证步骤
"""

import os
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.table import Table
from rich import box

# 加载环境变量
load_dotenv(PROJECT_ROOT / ".env")

from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline

console = Console()


class BatchIndexer:
    """批量文档索引器"""

    # 文档类别映射
    CATEGORIES = {
        "标准规范": "标准规范",
        "参考论文": "参考论文",
        "书籍报告": "书籍报告",
        "政策文件": "政策文件",
    }

    def __init__(
        self,
        force_reingest: bool = False,
        engine: str = "mineru",
        category_filter: Optional[str] = None,
        verbose: bool = False,
    ):
        """
        初始化批量索引器

        Args:
            force_reingest: 是否强制重新索引
            engine: OCR 引擎（mineru/marker）
            category_filter: 仅处理指定类别（None 表示处理所有）
            verbose: 是否显示详细日志
        """
        self.force_reingest = force_reingest
        self.engine = engine
        self.category_filter = category_filter
        self.verbose = verbose

        # 设置环境变量
        if force_reingest:
            os.environ["FORCE_REINGEST"] = "1"

        # 初始化 Pipeline
        console.print("\n[cyan]正在初始化索引 Pipeline...[/cyan]")
        self.pipeline = DocumentIngestionPipeline(engine=engine)
        console.print("[green]Pipeline 初始化完成[/green]\n")

        # 统计信息
        self.stats = {
            "total_files": 0,
            "success_files": 0,
            "skipped_files": 0,
            "failed_files": [],
            "start_time": None,
            "end_time": None,
        }

    def get_documents_directory(self) -> Path:
        """获取文档目录"""
        docs_dir = PROJECT_ROOT / "backend" / "databases" / "documents"
        if not docs_dir.exists():
            raise FileNotFoundError(f"文档目录不存在: {docs_dir}")
        return docs_dir

    def collect_pdf_files(self) -> List[tuple]:
        """
        收集所有待处理的 PDF 文件

        Returns:
            [(pdf_path, category), ...] 列表
        """
        docs_dir = self.get_documents_directory()
        pdf_files = []

        categories = self.CATEGORIES
        if self.category_filter:
            if self.category_filter not in categories:
                console.print(f"[red]错误: 未知的类别 '{self.category_filter}'[/red]")
                console.print(f"可用类别: {', '.join(categories.keys())}")
                return []
            categories = {self.category_filter: categories[self.category_filter]}

        for category_name, category_dir in categories.items():
            category_path = docs_dir / category_dir

            if not category_path.exists():
                console.print(f"[yellow]跳过不存在的目录: {category_path}[/yellow]")
                continue

            # 查找所有 PDF 文件
            pdfs = list(category_path.glob("*.pdf"))
            for pdf_file in pdfs:
                pdf_files.append((str(pdf_file), category_name))

            console.print(f"[dim]类别 '{category_name}': 找到 {len(pdfs)} 个文件[/dim]")

        return pdf_files

    def process_single_document(self, pdf_path: str, category: str) -> Dict:
        """
        处理单个文档

        Args:
            pdf_path: PDF 文件路径
            category: 文档类别

        Returns:
            处理结果字典
        """
        pdf_name = Path(pdf_path).name

        try:
            result = self.pipeline.process_document(
                pdf_path=pdf_path,
                category=category,
            )

            status = result.get("status", "unknown")

            if status == "success":
                self.stats["success_files"] += 1
                return {
                    "status": "success",
                    "name": pdf_name,
                    "mongo_doc_id": result.get("mongo_doc_id"),
                    "chunks": result.get("total_chunks", 0),
                }
            elif status == "skipped":
                self.stats["skipped_files"] += 1
                return {
                    "status": "skipped",
                    "name": pdf_name,
                    "reason": result.get("reason", "unknown"),
                }
            else:
                self.stats["failed_files"].append((pdf_name, f"状态: {status}"))
                return {
                    "status": "failed",
                    "name": pdf_name,
                    "error": result.get("reason", "unknown"),
                }

        except Exception as e:
            self.stats["failed_files"].append((pdf_name, str(e)))
            return {
                "status": "failed",
                "name": pdf_name,
                "error": str(e),
            }

    def run(self) -> Dict:
        """
        执行批量索引

        Returns:
            统计信息字典
        """
        self.stats["start_time"] = datetime.now()

        # 显示配置信息
        self._print_config()

        # 收集文件
        console.print("\n[cyan]正在扫描文档目录...[/cyan]")
        pdf_files = self.collect_pdf_files()

        if not pdf_files:
            console.print("[yellow]未找到任何 PDF 文件[/yellow]")
            return self.stats

        self.stats["total_files"] = len(pdf_files)
        console.print(f"[green]找到 {len(pdf_files)} 个 PDF 文件[/green]\n")

        # 处理文件
        console.print("=" * 80)
        console.print("[bold cyan]开始批量索引[/bold cyan]")
        console.print("=" * 80 + "\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:

            task = progress.add_task(
                "[cyan]处理文档...",
                total=len(pdf_files),
            )

            for idx, (pdf_path, category) in enumerate(pdf_files, 1):
                pdf_name = Path(pdf_path).name
                progress.update(
                    task,
                    description=f"[cyan]处理 [{idx}/{len(pdf_files)}]: {pdf_name[:50]}...",
                )

                result = self.process_single_document(pdf_path, category)

                if self.verbose:
                    self._print_result(idx, result)

                progress.advance(task)

        self.stats["end_time"] = datetime.now()

        # 显示结果
        self._print_summary()

        return self.stats

    def _print_config(self):
        """显示配置信息"""
        config_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
        config_table.add_column("Key", style="cyan")
        config_table.add_column("Value", style="white")

        config_table.add_row("OCR 引擎", self.engine)
        config_table.add_row("强制重新索引", "是" if self.force_reingest else "否")
        config_table.add_row("类别过滤", self.category_filter or "无（处理所有类别）")
        config_table.add_row("详细日志", "是" if self.verbose else "否")
        config_table.add_row("文档目录", str(self.get_documents_directory()))

        console.print(Panel(
            config_table,
            title="[bold]配置信息[/bold]",
            border_style="blue",
        ))

    def _print_result(self, idx: int, result: Dict):
        """打印单个文档的处理结果"""
        status = result["status"]
        name = result["name"]

        if status == "success":
            console.print(f"  [{idx}] [green]成功[/green] {name}")
            console.print(f"      MongoDB ID: {result.get('mongo_doc_id')}")
            console.print(f"      Chunks: {result.get('chunks', 0)}")
        elif status == "skipped":
            console.print(f"  [{idx}] [yellow]跳过[/yellow] {name}")
            console.print(f"      原因: {result.get('reason')}")
        else:
            console.print(f"  [{idx}] [red]失败[/red] {name}")
            console.print(f"      错误: {result.get('error', 'unknown')[:100]}")

    def _print_summary(self):
        """打印统计摘要"""
        duration = (self.stats["end_time"] - self.stats["start_time"]).total_seconds()

        # 统计表格
        stats_table = Table(
            show_header=True,
            box=box.ROUNDED,
            title="批量索引统计",
            title_style="bold green",
        )
        stats_table.add_column("指标", style="cyan", no_wrap=True)
        stats_table.add_column("数值", justify="right", style="yellow")

        stats_table.add_row("总文件数", str(self.stats["total_files"]))
        stats_table.add_row("成功", str(self.stats["success_files"]))
        stats_table.add_row("跳过", str(self.stats["skipped_files"]))
        stats_table.add_row("失败", str(len(self.stats["failed_files"])))
        stats_table.add_row("总耗时", f"{duration:.2f} 秒")

        if self.stats["success_files"] > 0:
            avg_time = duration / self.stats["success_files"]
            stats_table.add_row("平均速度", f"{avg_time:.2f} 秒/文档")

        console.print("\n" + "=" * 80)
        console.print(stats_table)
        console.print("=" * 80 + "\n")

        # 失败文件列表
        if self.stats["failed_files"]:
            console.print("[red]失败的文件:[/red]")
            for name, error in self.stats["failed_files"]:
                console.print(f"  - {name}")
                console.print(f"    [dim]{error[:100]}...[/dim]")
            console.print()

    def validate_results(self):
        """验证索引结果"""
        console.print("=" * 80)
        console.print("[cyan]验证索引结果...[/cyan]")
        console.print("=" * 80 + "\n")

        try:
            from pymongo import MongoClient

            mongo_uri = os.getenv('MONGODB_URI')
            db_name = os.getenv('MONGODB_DATABASE', 'mediarch')

            client = MongoClient(mongo_uri)
            db = client[db_name]

            # 检查 mediarch_chunks
            chunks_collection = db['mediarch_chunks']
            total_chunks = chunks_collection.count_documents({})
            with_path = chunks_collection.count_documents({'file_path': {'$exists': True, '$ne': None}})

            # 检查 documents
            docs_collection = db['documents']
            total_docs = docs_collection.count_documents({})

            # 显示结果
            result_table = Table(show_header=True, box=box.ROUNDED)
            result_table.add_column("数据库", style="cyan")
            result_table.add_column("集合", style="cyan")
            result_table.add_column("文档数", justify="right", style="yellow")

            result_table.add_row("MongoDB", "documents", str(total_docs))
            result_table.add_row("MongoDB", "mediarch_chunks", str(total_chunks))
            result_table.add_row("MongoDB", "chunks with file_path", f"{with_path} ({with_path/max(total_chunks,1)*100:.1f}%)")

            console.print(result_table)

            if total_docs > 0 and total_chunks > 0:
                console.print("\n[green]验证通过！[/green]")
                console.print(f"[dim]平均每个文档: {total_chunks/total_docs:.1f} chunks[/dim]\n")
            else:
                console.print("\n[yellow]警告: 数据库中没有文档[/yellow]\n")

        except Exception as e:
            console.print(f"[red]验证失败: {e}[/red]\n")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="批量文档索引器 - MediArch System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 批量索引所有文档
  python -m backend.cli.batch_indexer

  # 强制重新索引
  python -m backend.cli.batch_indexer --force

  # 仅处理标准规范
  python -m backend.cli.batch_indexer --category 标准规范

  # 详细模式
  python -m backend.cli.batch_indexer --force --verbose
        """
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新索引（忽略已存在的文档）"
    )

    parser.add_argument(
        "--category",
        type=str,
        choices=["标准规范", "参考论文", "书籍报告", "政策文件"],
        help="仅处理指定类别"
    )

    parser.add_argument(
        "--engine",
        type=str,
        default="mineru",
        choices=["mineru", "marker"],
        help="OCR 引擎（默认: mineru）"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示详细日志"
    )

    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="跳过最后的验证步骤"
    )

    args = parser.parse_args()

    # 显示标题
    console.print(Panel.fit(
        "[bold cyan]MediArch 批量文档索引器[/bold cyan]\n"
        "[dim]向量化 + MongoDB + Milvus[/dim]",
        border_style="cyan"
    ))

    try:
        # 创建索引器
        indexer = BatchIndexer(
            force_reingest=args.force,
            engine=args.engine,
            category_filter=args.category,
            verbose=args.verbose,
        )

        # 执行批量索引
        stats = indexer.run()

        # 验证结果
        if not args.skip_validation:
            indexer.validate_results()

        # 成功退出
        if stats["success_files"] > 0:
            console.print("[bold green]批量索引完成！[/bold green]\n")
            sys.exit(0)
        else:
            console.print("[yellow]没有成功处理任何文档[/yellow]\n")
            sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n\n[yellow]用户中断[/yellow]\n")
        sys.exit(130)

    except Exception as e:
        console.print(f"\n\n[red]错误: {e}[/red]\n")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
