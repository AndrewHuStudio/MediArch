import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime
import time
from typing import Optional
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.table import Table
from rich import box

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

# 设置临时目录到E盘（避免C盘占满）
temp_dir = os.getenv("TMPDIR") or os.getenv("TEMP") or os.getenv("TMP")
if temp_dir and Path(temp_dir).exists():
    tempfile.tempdir = temp_dir
    os.environ['TMPDIR'] = temp_dir
    os.environ['TEMP'] = temp_dir
    os.environ['TMP'] = temp_dir
    print(f"[INFO] Using temp directory: {temp_dir}")
else:
    print(f"[WARN] Custom temp directory not found, using default: {tempfile.gettempdir()}")

from backend.databases.graph.builders.kg_builder import MedicalKGBuilder

console = Console()


def check_disk_space():
    """构建前检查磁盘空间"""
    import shutil

    # 检查环境变量是否禁用磁盘检查
    skip_check = os.getenv("SKIP_DISK_CHECK", "0").lower() in {"1", "true", "yes"}
    if skip_check:
        console.print("[yellow][INFO] 磁盘空间检查已禁用（SKIP_DISK_CHECK=1）[/yellow]")
        return True

    # 检查临时目录所在盘的空间（优先检查）
    try:
        temp_path = Path(tempfile.gettempdir())
        temp_drive = temp_path.drive if temp_path.drive else "C:\\"
        usage = shutil.disk_usage(temp_drive)
        free_gb = usage.free / (1024**3)

        console.print(f"[cyan][INFO] 临时目录: {temp_path}[/cyan]")
        console.print(f"[cyan][INFO] 临时目录所在盘 ({temp_drive}) 剩余空间: {free_gb:.2f} GB[/cyan]")

        if free_gb < 5:
            console.print(f"[red][WARN] {temp_drive} 盘空间不足 {free_gb:.2f} GB[/red]")
            try:
                confirm = input("\n是否继续构建？(yes/no): ").strip().lower()
                if confirm not in ['yes', 'y']:
                    console.print("[yellow]已取消构建[/yellow]")
                    return False
            except (EOFError, KeyboardInterrupt):
                console.print("\n[yellow]已取消构建[/yellow]")
                return False
    except Exception as e:
        console.print(f"[yellow][WARN] 无法检查临时目录空间: {e}[/yellow]")

    # 检查C盘空间（仅警告，不阻止）
    try:
        usage = shutil.disk_usage("C:\\")
        free_gb = usage.free / (1024**3)

        if free_gb < 5:
            console.print(f"[yellow][WARN] C盘空间不足 {free_gb:.2f} GB[/yellow]")
            console.print("[dim]提示：Docker volumes 可能占用 C 盘空间[/dim]")
            console.print("[dim]建议：运行 python migrate_to_e_drive.py 迁移 Docker 数据到 E 盘[/dim]")
            console.print("[dim]或设置环境变量 SKIP_DISK_CHECK=1 跳过此检查[/dim]")
            # 仅警告，不阻止构建
        else:
            console.print(f"[green][OK] C盘剩余空间: {free_gb:.2f} GB[/green]")
    except Exception as e:
        console.print(f"[yellow][WARN] 无法检查C盘空间: {e}[/yellow]")

    return True


def estimate_deepseek_cost(total_chunks: int) -> tuple[float, dict]:
    """
    根据 deepseek-v3 的实际计费参数估算构建成本。

    默认假设：
      - 每个 chunk 的提示词 ~1500 tokens，输出 JSON ~620 tokens（可通过环境变量覆盖）
      - deepseek-v3 结算：提示价 $0.25/百万 tokens，补全价 $1.00/百万 tokens
      - 供应商倍率：提示 0.125，补全 4.0（来自定价面板，可通过环境变量覆盖）
    """
    if total_chunks <= 0:
        return 0.0, {}

    avg_prompt_tokens = float(os.getenv("KG_AVG_PROMPT_TOKENS", "1500"))
    avg_completion_tokens = float(os.getenv("KG_AVG_COMPLETION_TOKENS", "620"))
    prompt_multiplier = float(os.getenv("KG_PROMPT_TOKEN_MULTIPLIER", "0.125"))
    completion_multiplier = float(os.getenv("KG_COMPLETION_TOKEN_MULTIPLIER", "4.0"))
    prompt_price_per_million = float(os.getenv("KG_PROMPT_PRICE_PER_MTOK", "0.25"))
    completion_price_per_million = float(os.getenv("KG_COMPLETION_PRICE_PER_MTOK", "1.0"))

    billable_prompt_tokens = avg_prompt_tokens * prompt_multiplier
    billable_completion_tokens = avg_completion_tokens * completion_multiplier

    prompt_cost_per_chunk = (billable_prompt_tokens / 1_000_000) * prompt_price_per_million
    completion_cost_per_chunk = (billable_completion_tokens / 1_000_000) * completion_price_per_million
    cost_per_chunk = prompt_cost_per_chunk + completion_cost_per_chunk
    total_cost = total_chunks * cost_per_chunk

    details = {
        "prompt_tokens": avg_prompt_tokens,
        "completion_tokens": avg_completion_tokens,
        "prompt_multiplier": prompt_multiplier,
        "completion_multiplier": completion_multiplier,
        "prompt_price_per_million": prompt_price_per_million,
        "completion_price_per_million": completion_price_per_million,
        "cost_per_chunk": cost_per_chunk,
    }

    return total_cost, details


def check_and_clear_failed_cache(builder):
    """自动检查并清除失败的缓存记录和空结果的缓存"""
    try:
        # 清除标记为失败的缓存
        failed_count = builder.extractions_collection.count_documents({
            "version": builder.extraction_version,
            "status": "failed"
        })

        if failed_count > 0:
            console.print(f"[yellow][INFO] 发现 {failed_count} 条失败的缓存记录，正在清除...[/yellow]")
            result = builder.extractions_collection.delete_many({
                "version": builder.extraction_version,
                "status": "failed"
            })
            console.print(f"[green][OK] 已清除 {result.deleted_count} 条失败的缓存记录[/green]")

        # 清除空结果的缓存（entities为空的）
        empty_count = builder.extractions_collection.count_documents({
            "version": builder.extraction_version,
            "status": "success",
            "$or": [
                {"result.entities": {}},
                {"result.entities": {"$exists": False}},
                {"result.entities": None}
            ]
        })

        if empty_count > 0:
            console.print(f"[yellow][INFO] 发现 {empty_count} 条空结果的缓存记录，正在清除...[/yellow]")
            result = builder.extractions_collection.delete_many({
                "version": builder.extraction_version,
                "status": "success",
                "$or": [
                    {"result.entities": {}},
                    {"result.entities": {"$exists": False}},
                    {"result.entities": None}
                ]
            })
            console.print(f"[green][OK] 已清除 {result.deleted_count} 条空结果的缓存记录[/green]")

        if failed_count == 0 and empty_count == 0:
            console.print("[green][OK] 没有需要清除的缓存记录[/green]")

        console.print()  # 空行

    except Exception as e:
        console.print(f"[red][WARN] 清除缓存时出错: {e}[/red]\n")


def main():
    console.print(Panel.fit(
        "[bold cyan]医疗建筑知识图谱构建[/bold cyan]\n[dim]DeepSeek V3[/dim]",
        border_style="cyan"
    ))

    # 检查磁盘空间
    if not check_disk_space():
        return

    console.print()  # 空行

    # 验证环境变量（支持两种前缀：KG_OPENAI_* 或 OPENAI_*）
    api_key = os.getenv("KG_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("KG_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    model = os.getenv("KG_OPENAI_MODEL") or os.getenv("OPENAI_MODEL")

    required_vars = {
        "API Key": api_key,
        "API Base URL": base_url,
        "Model": model,
        "MongoDB URI": os.getenv("MONGODB_URI"),
        "Neo4j URI": os.getenv("NEO4J_URI"),
    }

    missing_vars = [name for name, value in required_vars.items() if not value]

    if missing_vars:
        console.print(f"[red]✗ 缺少以下配置：{', '.join(missing_vars)}[/red]")
        console.print("\n[yellow]请在 .env 文件中配置以下变量：[/yellow]")
        if "API Key" in missing_vars:
            console.print("  OPENAI_API_KEY=your-api-key")
        if "API Base URL" in missing_vars:
            console.print("  OPENAI_BASE_URL=https://api.openai.com/v1")
        if "Model" in missing_vars:
            console.print("  OPENAI_MODEL=deepseek-v3")
        if "MongoDB URI" in missing_vars:
            console.print("  MONGODB_URI=mongodb://...")
        if "Neo4j URI" in missing_vars:
            console.print("  NEO4J_URI=bolt://localhost:7687")
        return

    # Schema 路径
    schema_path = os.getenv("KG_SCHEMA_PATH", "backend/databases/graph/schemas/medical_architecture.json")

    # 配置表格
    config_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    config_table.add_column("Key", style="cyan")
    config_table.add_column("Value", style="white")
    config_table.add_row("LLM模型", model)
    config_table.add_row("API Base", base_url)
    config_table.add_row("MongoDB", os.getenv('MONGODB_URI').split('@')[0]+'@...')
    config_table.add_row("Neo4j", os.getenv('NEO4J_URI'))
    console.print(Panel(config_table, title="[bold]配置信息[/bold]", border_style="blue"))
    
    start_time = datetime.now()
    
    try:
        # 步骤0：先初始化构建器（这会触发构建策略选择）
        # 必须在 Progress 之前完成，以确保用户输入正常工作
        console.print("\n[dim]正在初始化构建器...[/dim]")
        builder = MedicalKGBuilder(schema_path=schema_path)
        console.print("[green][OK] 构建器初始化完成[/green]\n")

        # 步骤0.5：检查并清除失败的缓存
        check_and_clear_failed_cache(builder)

        total_chunks = builder.chunks_collection.count_documents({})
        existing_processed = builder.extractions_collection.count_documents(
            {"version": builder.extraction_version}
        )
        completed_initial = min(existing_processed, total_chunks)
        extract_start_ts = time.time()

        def format_eta(seconds: Optional[float]) -> str:
            if seconds is None or seconds == float("inf"):
                return "--"
            if seconds < 0:
                seconds = 0
            hrs, rem = divmod(int(seconds), 3600)
            mins, secs = divmod(rem, 60)
            if hrs > 0:
                return f"{hrs:02d}:{mins:02d}:{secs:02d}"
            return f"{mins:02d}:{secs:02d}"

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            chunk_task = progress.add_task(
                "[cyan]从MongoDB读取并抽取实体关系...",
                total=max(total_chunks, 1),
                completed=completed_initial,
            )

            def progress_callback(processed_run: int, success_run: int, skipped_run: int, failed_run: int) -> None:
                completed = min(completed_initial + processed_run, total_chunks)
                remaining = max(total_chunks - completed, 0)
                processed_for_eta = processed_run if processed_run > 0 else 0
                elapsed = max(time.time() - extract_start_ts, 1e-3)
                rate = processed_for_eta / elapsed
                eta = remaining / rate if rate > 0 else None
                desc = (
                    f"[cyan]抽取进度 {completed}/{total_chunks} | "
                    f"剩余 {remaining} | 成功 {success_run} 跳过 {skipped_run} 失败 {failed_run} | ETA {format_eta(eta)}"
                )
                progress.update(chunk_task, completed=completed, description=desc)

            # 步骤2：读取与抽取
            stats = builder.build_from_mongodb(progress_callback=progress_callback)
            progress.update(chunk_task, completed=max(total_chunks, 1), description="[green]✓ 实体关系抽取完成")
            
            # 步骤3：写入数据库
            write_task = progress.add_task("[cyan]写入Neo4j...", total=1)
            builder.write_to_databases()
            progress.update(write_task, advance=1, description="[green]✓ 数据写入完成")
        
        # 统计信息
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # 统计表格
        stats_table = Table(show_header=True, box=box.ROUNDED, title="构建统计", title_style="bold green")
        stats_table.add_column("指标", style="cyan", no_wrap=True)
        stats_table.add_column("数值", justify="right", style="yellow")
        
        stats_table.add_row("处理chunks数", str(stats.get('total_chunks', 0)))
        stats_table.add_row("成功chunks数", str(stats.get('success_chunks', 0)))
        stats_table.add_row("NetworkX节点数", str(stats.get('total_nodes', 0)))
        stats_table.add_row("NetworkX边数", str(stats.get('total_edges', 0)))
        stats_table.add_row("总耗时", f"{duration:.2f}秒")
        stats_table.add_row("平均速度", f"{stats.get('total_chunks', 0) / max(duration / 60, 0.01):.2f} chunks/分钟")
        
        write_summary = getattr(builder, "last_write_summary", {}) or {}
        if write_summary:
            stats_table.add_row("Neo4j节点数", str(write_summary.get('neo4j_nodes', '未知')))
            stats_table.add_row("Neo4j边数", str(write_summary.get('neo4j_edges', '未知')))
        
        console.print(stats_table)
        
        # 估算成本（基于测试数据）
        estimated_cost, cost_details = estimate_deepseek_cost(stats.get('total_chunks', 0))
        console.print(f"\n[bold cyan]💰 估算成本：[/bold cyan][yellow]${estimated_cost:.4f} USD[/yellow]")
        if cost_details:
            console.print(
                f"[dim]假设：提示 {cost_details['prompt_tokens']:.0f} tokens × 倍率 {cost_details['prompt_multiplier']}, "
                f"补全 {cost_details['completion_tokens']:.0f} tokens × 倍率 {cost_details['completion_multiplier']}，"
                f"单chunk ≈ ${cost_details['cost_per_chunk']:.6f}[/dim]\n"
            )
        else:
            console.print()
        
        # 后续步骤
        next_steps = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
        next_steps.add_column("Step", style="bold cyan", width=3)
        next_steps.add_column("Action", style="white")
        next_steps.add_row("1.", "验证Neo4j数据：[link=http://localhost:7474]http://localhost:7474[/link]")
        next_steps.add_row("2.", "查看Neo4j Browser进行可视化查询")
        next_steps.add_row("3.", "测试Agentic RAG系统集成")
        console.print(Panel(next_steps, title="[bold green]✓ 构建完成！后续步骤[/bold green]", border_style="green"))
        
    except KeyboardInterrupt:
        console.print("\n\n[yellow]⚠ 构建被用户中断[/yellow]")
    except Exception as e:
        console.print(f"\n\n[red]✗ 构建失败：{e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
