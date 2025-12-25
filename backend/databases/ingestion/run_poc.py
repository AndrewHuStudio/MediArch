# backend/databases/ingestion/run_poc.py
import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime
import time

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline
from backend.databases.ingestion.ocr.mineru_client import MineruClient
from backend.databases.ingestion.ocr.ocr_progress_tracker import get_tracker

# Rich 库用于彩色输出和进度条
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
    from rich.panel import Panel
    from rich.table import Table
    from rich.live import Live
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None

# Tqdm 备用进度条
try:
    from tqdm import tqdm
    from tqdm.contrib.logging import logging_redirect_tqdm
except Exception:
    tqdm = None  # type: ignore
    logging_redirect_tqdm = None  # type: ignore

# 创建全局 console
console = Console() if RICH_AVAILABLE else None

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
    sys.stderr.reconfigure(encoding='utf-8', errors='ignore')
except Exception:
    pass

# 抑制 pypdf 重复键告警（/MediaBox）
try:
    import warnings
    warnings.filterwarnings(
        "ignore",
        message=r"Multiple definitions in dictionary.*for key /MediaBox",
        category=Warning,
        module=r"pypdf.*",
    )
except Exception:
    pass


# =============================
# 彩色可视化辅助函数
# =============================
def print_success(msg: str):
    """打印成功消息（绿色）"""
    if RICH_AVAILABLE and console:
        console.print(f"✓ {msg}", style="bold green")
    else:
        print(f"[OK] {msg}")


def print_info(msg: str):
    """打印信息消息（蓝色）"""
    if RICH_AVAILABLE and console:
        console.print(f"ℹ {msg}", style="bold cyan")
    else:
        print(f"[INFO] {msg}")


def print_warning(msg: str):
    """打印警告消息（黄色）"""
    if RICH_AVAILABLE and console:
        console.print(f"⚠ {msg}", style="bold yellow")
    else:
        print(f"[WARN] {msg}")


def print_error(msg: str):
    """打印错误消息（红色）"""
    if RICH_AVAILABLE and console:
        console.print(f"✗ {msg}", style="bold red")
    else:
        print(f"[ERROR] {msg}")


def print_header(title: str, subtitle: str = ""):
    """打印彩色标题"""
    if RICH_AVAILABLE and console:
        panel_content = f"[bold white]{title}[/bold white]"
        if subtitle:
            panel_content += f"\n[dim]{subtitle}[/dim]"
        console.print(Panel(panel_content, style="bold cyan", box=box.DOUBLE))
    else:
        print("\n" + "=" * 70)
        print(title)
        if subtitle:
            print(subtitle)
        print("=" * 70)


def format_time(seconds: float) -> str:
    """格式化时间显示"""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}分钟"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}小时"


def create_summary_table(stats: dict) -> None:
    """创建彩色统计表格"""
    if RICH_AVAILABLE and console:
        table = Table(title="📊 处理统计", box=box.ROUNDED, show_header=True, header_style="bold magenta")
        table.add_column("项目", style="cyan", no_wrap=True)
        table.add_column("数值", justify="right", style="green")
        
        for key, value in stats.items():
            table.add_row(key, str(value))
        
        console.print(table)
    else:
        print("\n" + "-" * 60)
        print("处理统计")
        print("-" * 60)
        for key, value in stats.items():
            print(f"{key}: {value}")
        print("-" * 60)

def _parse_page_range_env() -> tuple | None:
    """
    读取环境变量设置的页段限制，形如：INGEST_PAGE_RANGE=1-10
    返回 (start, end) 或 None（表示全量页）
    """
    v = os.getenv("INGEST_PAGE_RANGE") or os.getenv("PAGE_RANGE")
    if not v:
        return None
    try:
        s, e = v.replace(" ", "").split("-")
        s, e = int(s), int(e)
        if s <= 0 or e < s:
            return None
        return (s, e)
    except Exception:
        return None

def _list_pdfs(docs_dir: str) -> list[tuple[str, str]]:
    root = Path(docs_dir).resolve()
    pdfs: list[tuple[str, str]] = []
    subs = [d for d in root.iterdir() if d.is_dir()]
    if subs:
        for d in subs:
            for p in d.glob("*.pdf"):
                pdfs.append((str(p.resolve()), d.name))
    else:
        for p in root.glob("*.pdf"):
            pdfs.append((str(p.resolve()), root.name))
    return pdfs

def _local_total_pages(pdf: str) -> int:
    try:
        try:
            from pypdf import PdfReader
            return len(PdfReader(pdf).pages)
        except Exception:
            pass
        try:
            from PyPDF2 import PdfReader
            return len(PdfReader(pdf).pages)
        except Exception:
            pass
        try:
            import fitz  # PyMuPDF
            return len(fitz.open(pdf))
        except Exception:
            pass
    except Exception:
        pass
    return -1

def _preload_totals(tracker, docs_dir: str) -> None:
    pdfs = _list_pdfs(docs_dir)
    dirty = False
    for abs_path, category in pdfs:
        rec = tracker.records.get(abs_path)
        tp_local = _local_total_pages(abs_path)
        if tp_local <= 0:
            continue
        if rec is None:
            from backend.databases.ingestion.ocr.ocr_progress_tracker import DocumentScanRecord
            tracker.records[abs_path] = DocumentScanRecord(
                file_path=abs_path,
                file_name=os.path.basename(abs_path),
                category=category,
                total_pages=tp_local,
                scanned_pages=0,
                status='partial',
            )
            dirty = True
        else:
            existing = rec.total_pages or -1
            if existing != tp_local:
                rec.total_pages = tp_local
                dirty = True
    if dirty:
        tracker._save_progress()

def _ensure_total_pages(tracker, pipe: DocumentIngestionPipeline, pdf_path: str, category: str) -> int:
    rec = tracker.records.get(pdf_path)
    if rec and rec.total_pages and rec.total_pages > 0:
        return int(rec.total_pages)
    tp_local = _local_total_pages(pdf_path)
    if tp_local > 0:
        if rec:
            rec.total_pages = tp_local
            tracker._save_progress()
        else:
            from backend.databases.ingestion.ocr.ocr_progress_tracker import DocumentScanRecord
            tracker.records[pdf_path] = DocumentScanRecord(
                file_path=pdf_path,
                file_name=os.path.basename(pdf_path),
                category=category,
                total_pages=tp_local,
                scanned_pages=0,
                status='partial',
            )
            tracker._save_progress()
        return tp_local
    return -1

def _gather_index(tracker, docs_dir: str):
    pdfs = _list_pdfs(docs_dir)
    from collections import defaultdict
    data = defaultdict(lambda: {"NEW": [], "IN-PROGRESS": [], "COMPLETED": [], "FAILED": []})
    totals = {"NEW": 0, "IN-PROGRESS": 0, "COMPLETED": 0, "FAILED": 0, "ALL": len(pdfs)}
    for abs_path, category in pdfs:
        name = os.path.basename(abs_path)
        rec = tracker.records.get(abs_path)
        if rec is None:
            item = {"name": name, "path": abs_path, "total": None, "scanned": 0, "pending": None, "last": None}
            data[category]["NEW"].append(item)
            totals["NEW"] += 1
            continue
        # compute scanned/total/pending
        scanned = 0
        for a, b in (rec.done_ranges or []):
            scanned += (b - a + 1)
        scanned = max(scanned, rec.scanned_pages or 0)
        total = rec.total_pages if rec.total_pages and rec.total_pages > 0 else None
        pending = (max(total - scanned, 0) if total else None)
        status = (rec.status or "partial").lower()
        if status == "failed":
            key = "FAILED"
        elif total and pending == 0:
            key = "COMPLETED"
        else:
            key = "IN-PROGRESS"
        item = {"name": rec.file_name, "path": abs_path, "total": total, "scanned": scanned, "pending": pending, "last": _fmt_hm(rec.last_ocr_at)}
        data[category][key].append(item)
        totals[key] += 1
    return data, totals

def _fmt_hm(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        s = str(ts).replace("T", " ").strip()
        # 截到“YYYY-MM-DD HH:MM”
        return s[:16] if len(s) >= 16 else s
    except Exception:
        return "-"

def _show_overview(tracker, docs_dir: str, collapse: bool = True, limit_per_group: int = 10, engine: str | None = None):
    data, totals = _gather_index(tracker, docs_dir)
    print("\n" + "-" * 60)
    header = "当前进度总览"
    if engine:
        # 若为 mineru，显示后端与设备
        if engine.strip().lower() == "mineru":
            try:
                mc = MineruClient(
                    project_root=os.getenv("MINERU_PROJECT_ROOT"),
                    mineru_exe=os.getenv("MINERU_EXE", "mineru"),
                    python_exe=os.getenv("MINERU_PYTHON_EXE", "python"),
                    backend=os.getenv("MINERU_BACKEND", "pipeline"),
                    use_cuda=(os.getenv("MINERU_USE_CUDA", "0").lower() in {"1", "true"}),
                )
                header += f"  |  引擎: mineru  |  后端: {mc.get_backend()}  |  设备: {mc.get_device_mode()}"
            except Exception:
                header += "  |  引擎: mineru"
        else:
            header += f"  |  当前引擎: {engine}"
    print(header)
    print("-" * 60)
    print(f"文件数: {totals['ALL']} | NEW: {totals['NEW']} | IN-PROGRESS: {totals['IN-PROGRESS']} | COMPLETED: {totals['COMPLETED']} | FAILED: {totals['FAILED']}")
    if totals['ALL'] == 0:
        print("(documents 目录为空，请放入 PDF 文件后重试)\n")
        return
    print("")
    order = ["NEW", "IN-PROGRESS", "COMPLETED", "FAILED"]
    for cat in sorted(data.keys()):
        print(f"[{cat}]")
        for group in order:
            items = sorted(data[cat][group], key=lambda x: x["name"].lower())
            if not items:
                continue
            print(f"  {group}  ({len(items)} files)")
            shown = items[:limit_per_group] if collapse else items
            for it in shown:
                if it["total"]:
                    print(f"    {it['name']}  完成:{it['scanned']}/{it['total']}  待完成:{it['pending']}/{it['total']}  更新时间:{(it['last'] or '-')}")
                else:
                    print(f"    {it['name']}  完成:{it['scanned']}/?  待完成:?/?  更新时间:{(it['last'] or '-')}")
            if collapse and len(items) > limit_per_group:
                print(f"    ... and {len(items) - limit_per_group} more")
        print("")

def _export_markdown_report(tracker, docs_dir: str) -> str:
    from datetime import datetime
    data, totals = _gather_index(tracker, docs_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("backend/databases/ingestion/ocr_res")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ocr_report_{ts}.md"
    lines: list[str] = []
    lines.append(f"# OCR 录入进度报告 ({ts})\n")
    lines.append(f"- 总文件数: {totals['ALL']}")
    lines.append(f"- NEW: {totals['NEW']}  IN-PROGRESS: {totals['IN-PROGRESS']}  COMPLETED: {totals['COMPLETED']}  FAILED: {totals['FAILED']}\n")
    order = ["NEW", "IN-PROGRESS", "COMPLETED", "FAILED"]
    for cat in sorted(data.keys()):
        lines.append(f"## {cat}\n")
        for group in order:
            items = sorted(data[cat][group], key=lambda x: x["name"].lower())
            if not items:
                continue
            lines.append(f"### {group} ({len(items)})\n")
            lines.append("文件名 | 状态 | 完成 | 待完成 | 总页 | 引擎 | 更新时间 | 路径")
            lines.append("---|---|---:|---:|---:|---|---|---")
            for it in items:
                total = it["total"] if it["total"] else "?"
                scanned = it["scanned"]
                pending = it["pending"] if it["total"] else "?"
                last = it["last"] or "-"
                # 从账本取 engine（如果存在）
                rec = tracker.records.get(it["path"]) if hasattr(tracker, "records") else None
                engine = getattr(rec, "engine", None) if rec else None
                engine = engine or "-"
                lines.append(f"{it['name']} | {group} | {scanned} | {pending} | {total} | {engine} | {last} | {it['path']}")
            lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)

def _light_increment(docs_dir: str, span: int = 3, engine: str | None = None):
    tracker = get_tracker()
    pdfs = _list_pdfs(docs_dir)
    pipe = DocumentIngestionPipeline(engine=engine)
    
    run_start = time.time()
    total_files = 0
    total_pages = 0
    
    print_header("🚀 轻录入模式", f"每个文档处理 {span} 页 | 引擎: {engine or 'default'}")
    try:
        if RICH_AVAILABLE and console:
            # 使用 Rich 进度条
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("•"),
                TextColumn("[cyan]{task.completed}/{task.total} 文件"),
                TimeElapsedColumn(),
            ) as progress:
                task = progress.add_task("📄 轻录入处理", total=len(pdfs))
                for i, (path, category) in enumerate(pdfs, 1):
                    rec = tracker.records.get(path)
                    tp = _ensure_total_pages(tracker, pipe, path, category)
                    if tp <= 0:
                        print_warning(f"跳过: 无法获取总页数 - {os.path.basename(path)}")
                        progress.update(task, advance=1)
                        continue
                    done = (rec and rec.done_ranges) or []
                    pending_ranges = tracker._compute_pending(tp, done)
                    if not pending_ranges:
                        progress.update(task, advance=1)
                        continue
                    s, e = pending_ranges[0]
                    end = min(s + span - 1, e)
                    console.print(f"  [dim]→[/dim] [cyan]{os.path.basename(path)}[/cyan] [dim]页段 {s}-{end}[/dim]")
                    _ = pipe.process_document(pdf_path=path, category=category, page_range=(s, end))
                    total_files += 1
                    total_pages += max(0, min(end, tp) - max(1, s) + 1)
                    progress.update(task, advance=1)
        else:
            # 备用：使用 tqdm 或简单输出
            iterator = enumerate(pdfs, 1)
            if tqdm:
                pbar = tqdm(total=len(pdfs), desc="轻录入：文档处理", unit="file")
                for i, (path, category) in iterator:
                    rec = tracker.records.get(path)
                    tp = _ensure_total_pages(tracker, pipe, path, category)
                    if tp <= 0:
                        print(f"[跳过] 无法获取总页数: {path}")
                        pbar.update(1)
                        continue
                    done = (rec and rec.done_ranges) or []
                    pending_ranges = tracker._compute_pending(tp, done)
                    if not pending_ranges:
                        pbar.update(1)
                        continue
                    s, e = pending_ranges[0]
                    end = min(s + span - 1, e)
                    print(f"[轻录入] {path} -> {s}-{end}")
                    _ = pipe.process_document(pdf_path=path, category=category, page_range=(s, end))
                    total_files += 1
                    total_pages += max(0, min(end, tp) - max(1, s) + 1)
                    pbar.update(1)
                pbar.close()
            else:
                for i, (path, category) in iterator:
                    rec = tracker.records.get(path)
                    tp = _ensure_total_pages(tracker, pipe, path, category)
                    if tp <= 0:
                        print(f"[跳过] 无法获取总页数: {path}")
                        continue
                    done = (rec and rec.done_ranges) or []
                    pending_ranges = tracker._compute_pending(tp, done)
                    if not pending_ranges:
                        continue
                    s, e = pending_ranges[0]
                    end = min(s + span - 1, e)
                    print(f"[轻录入] {path} -> {s}-{end}")
                    _ = pipe.process_document(pdf_path=path, category=category, page_range=(s, end))
                    total_files += 1
                    total_pages += max(0, min(end, tp) - max(1, s) + 1)
    finally:
        pipe.close()
    
    run_end = time.time()
    elapsed = run_end - run_start
    rem_files, rem_pages = _summarize_pending(get_tracker(), docs_dir)
    
    # 创建统计表格
    stats = {
        "⏱️  总耗时": format_time(elapsed),
        "📁 处理文件数": total_files,
        "📄 处理页数": total_pages,
        "⚡ 平均速度": f"{total_pages / elapsed:.1f} 页/秒" if elapsed > 0 else "N/A",
        "📋 剩余文件": rem_files,
        "📃 剩余页数": rem_pages,
    }
    
    print_success("轻录入完成！")
    create_summary_table(stats)

def _fill_all(docs_dir: str, span: int = 100000, engine: str | None = None):
    tracker = get_tracker()
    pdfs = _list_pdfs(docs_dir)
    pipe = DocumentIngestionPipeline(engine=engine)

    run_start = datetime.now()
    total_files = 0
    total_pages = 0
    
    print_header("🔥 补齐模式", f"处理所有缺口 | 引擎: {engine or 'default'}")
    try:
        iterator = enumerate(pdfs, 1)
        if logging_redirect_tqdm:
            with logging_redirect_tqdm():
                files_pbar = tqdm(total=len(pdfs), desc="补齐：文档处理", unit="file") if tqdm else None
                for i, (path, category) in iterator:
                    if tqdm:
                        tqdm.write(f"\n[补齐] {i}/{len(pdfs)} -> {path}")
                    else:
                        print(f"\n[补齐] {i}/{len(pdfs)} -> {path}")
                    rec = tracker.records.get(path)
                    tp = _ensure_total_pages(tracker, pipe, path, category)
                    if tp <= 0:
                        print("  [WARN] 未能获取总页数，跳过")
                        if files_pbar: files_pbar.update(1)
                        continue
                    done = (rec and rec.done_ranges) or []
                    pending_ranges = tracker._compute_pending(tp, done)
                    if not pending_ranges:
                        if tqdm:
                            tqdm.write("  已无缺口，跳过")
                        else:
                            print("  已无缺口，跳过")
                        if files_pbar: files_pbar.update(1)
                        continue
                    # 计算需要处理的总页数，用于页级进度条
                    plan_pages = 0
                    for s, e in pending_ranges:
                        cur = s
                        while cur <= e:
                            end = min(cur + span - 1, e)
                            plan_pages += max(0, min(end, tp) - max(1, cur) + 1)
                            cur = end + 1
                    pages_pbar = tqdm(total=plan_pages, desc="页段处理", unit="page") if tqdm else None
                    for s, e in pending_ranges:
                        cur = s
                        while cur <= e:
                            end = min(cur + span - 1, e)
                            if tqdm:
                                tqdm.write(f"  [页段] {cur}-{end}")
                            else:
                                print(f"  [页段] {cur}-{end}")
                            _ = pipe.process_document(pdf_path=path, category=category, page_range=(cur, end))
                            inc = max(0, min(end, tp) - max(1, cur) + 1)
                            total_pages += inc
                            if pages_pbar: pages_pbar.update(inc)
                            cur = end + 1
                    if pages_pbar: pages_pbar.close()
                    total_files += 1
                    if files_pbar: files_pbar.update(1)
                if files_pbar: files_pbar.close()
        else:
            for i, (path, category) in iterator:
                print(f"\n[补齐] {i}/{len(pdfs)} -> {path}")
                rec = tracker.records.get(path)
                tp = _ensure_total_pages(tracker, pipe, path, category)
                if tp <= 0:
                    print("  [WARN] 未能获取总页数，跳过")
                    continue
                done = (rec and rec.done_ranges) or []
                pending_ranges = tracker._compute_pending(tp, done)
                if not pending_ranges:
                    print("  已无缺口，跳过")
                    continue
                for s, e in pending_ranges:
                    cur = s
                    while cur <= e:
                        end = min(cur + span - 1, e)
                        print(f"  [页段] {cur}-{end}")
                        _ = pipe.process_document(pdf_path=path, category=category, page_range=(cur, end))
                        total_pages += max(0, min(end, tp) - max(1, cur) + 1)
                        cur = end + 1
                total_files += 1
    finally:
        pipe.close()
    run_end = datetime.now()
    print("\n" + "-" * 60)
    print("本次补齐统计")
    print("-" * 60)
    print(f"时间: {run_start.strftime('%Y-%m-%d %H:%M')}  ->  {run_end.strftime('%H:%M')}")
    print(f"处理文件: {total_files}  |  处理页数: {total_pages}")
    rem_files, rem_pages = _summarize_pending(get_tracker(), docs_dir)
    print(f"尚未录入: {rem_files} 个文件，{rem_pages} 页")
    print("-" * 60 + "\n")

def _estimate_work(tracker, docs_dir: str, mode: str) -> tuple[int, int]:
    """返回 (将处理文件数, 预计处理页数)"""
    pdfs = _list_pdfs(docs_dir)
    count_files = 0
    count_pages = 0
    for path, category in pdfs:
        rec = tracker.records.get(path)
        total = (rec and rec.total_pages) or None
        done = (rec and rec.done_ranges) or []
        scanned = sum((b - a + 1) for a, b in (done or []))
        if total and scanned >= total:
            continue
        # treat NEW or IN-PROGRESS
        count_files += 1
        if total:
            if mode == "light":
                # 取第一个缺口的最多 3 页
                pending_ranges = tracker._compute_pending(total, done)
                if pending_ranges:
                    s, e = pending_ranges[0]
                    count_pages += min(3, e - s + 1)
            else:
                # 所有缺口页之和
                count_pages += (total - scanned)
    return count_files, count_pages

def _summarize_pending(tracker, docs_dir: str) -> tuple[int, int]:
    """汇总当前未完成的文件数与页数。"""
    pdfs = _list_pdfs(docs_dir)
    files = 0
    pages = 0
    for path, category in pdfs:
        rec = tracker.records.get(path)
        if not rec:
            tp = _local_total_pages(path)
            if tp and tp > 0:
                files += 1
                pages += tp
            continue
        scanned = sum((b - a + 1) for a, b in (rec.done_ranges or []))
        scanned = max(scanned, rec.scanned_pages or 0)
        tp = rec.total_pages or 0
        if tp and scanned < tp:
            files += 1
            pages += (tp - scanned)
    return files, pages

def _interactive():
    docs_dir = os.getenv("DOCS_DIR", "backend/databases/documents")
    tracker = get_tracker()
    
    print_header("📚 MediArch OCR 交互模式", "智能文档处理系统")
    
    # 首先选择 OCR 引擎
    current_engine = os.getenv("OCR_ENGINE", "mineru").strip().lower()
    while True:
        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]选择 OCR 引擎：[/bold cyan]")
            console.print("  [green]1)[/green] TextIn ")
            console.print("  [green]2)[/green] MinerU ")
            console.print(f"  [dim]回车继续 (当前: {current_engine})[/dim]")
        else:
            print("\n选择 OCR 引擎：")
            print("  1) TextIn")
            print("  2) MinerU")
            print("  回车直接继续 (当前: %s)" % current_engine)
        
        eg = input("输入序号并回车: ").strip()
        if eg == "1":
            current_engine = "textin"
            print_success(f"已选择: TextIn")
            break
        elif eg == "2":
            current_engine = "mineru"
            print_success(f"已选择: MinerU")
            break
        elif eg == "":
            break
        else:
            print_warning("无效输入，请重试。")
    # 先为所有文档补齐总页数
    _preload_totals(tracker, docs_dir)
    _show_overview(tracker, docs_dir, collapse=True, limit_per_group=10, engine=current_engine)
    
    while True:
        if RICH_AVAILABLE and console:
            console.print("\n[bold magenta]请选择操作：[/bold magenta]")
            console.print("  [green]1)[/green] 🚀 轻录入（每个文档补 3 页）")
            console.print("  [green]2)[/green] 🔥 补齐全部缺口（一次性跑完剩余页）")
            console.print("  [green]3)[/green] 📊 导出 Markdown 报告")
            console.print("  [green]4)[/green] 👀 仅查看当前进度")
            console.print("  [green]0)[/green] 🚪 退出")
        else:
            print("\n请选择操作：")
            print("  1) 轻录入（每个文档补 3 页）")
            print("  2) 补齐全部缺口（一次性跑完剩余页）")
            print("  3) 导出 Markdown 报告")
            print("  4) 仅查看当前进度")
            print("  0) 退出")
        
        choice = input("输入序号并回车: ").strip()
        if choice == "1":
            n, pages = _estimate_work(tracker, docs_dir, mode="light")
            print_info(f"将处理约 {n} 个文件，共 {pages} 页")
            yn = input("是否继续? (y/N): ").strip().lower()
            if yn == "y":
                _light_increment(docs_dir, span=3, engine=current_engine)
            _show_overview(get_tracker(), docs_dir, collapse=True, limit_per_group=10, engine=current_engine)
        elif choice == "2":
            n, pages = _estimate_work(tracker, docs_dir, mode="fill")
            print_info(f"将处理约 {n} 个文件，共 {pages} 页")
            yn = input("是否继续? (y/N): ").strip().lower()
            if yn == "y":
                _fill_all(docs_dir, span=100000, engine=current_engine)
            _show_overview(get_tracker(), docs_dir, collapse=True, limit_per_group=10, engine=current_engine)
        elif choice == "3":
            path = _export_markdown_report(get_tracker(), docs_dir)
            print_success(f"已导出报告: {path}")
        elif choice == "4":
            _show_overview(get_tracker(), docs_dir, collapse=True, limit_per_group=10, engine=current_engine)
        elif choice == "0":
            print_success("退出。再见！")
            break
        else:
            print_warning("无效输入，请重试。")

def main():
    load_dotenv()

    # 可配置目录与页段
    docs_dir = os.getenv("DOCS_DIR", "backend/databases/documents")
    page_range = _parse_page_range_env()  # 例如 1-10，None 表示全量

    # 交互模式：默认开启；仅当设置 RUN_POC_INTERACTIVE=0 时跳过
    if os.getenv("RUN_POC_INTERACTIVE", "1") not in {"0", "false", "False"}:
        _interactive()
        return

    print("\n" + "=" * 70)
    print("MediArch Document Ingestion - Batch Mode")
    print("=" * 70)
    print(f"扫描目录：{docs_dir}")
    print(f"页段限制：{page_range or '全量页'}")
    print("=" * 70 + "\n")

    tracker = get_tracker()
    # 预加载所有文档的总页数，避免账本中存在旧值
    _preload_totals(tracker, docs_dir)
    SPAN = int(os.getenv("RUN_POC_SPAN", "10"))

    print("批处理模式：按账本缺口增量补齐\n")

    pipe = DocumentIngestionPipeline(engine=os.getenv("OCR_ENGINE"))

    try:
        # 构建 PDF 列表（根目录或叶子目录均可）
        root = Path(docs_dir).resolve()
        pdfs: list[tuple[str, str]] = []
        subs = [d for d in root.iterdir() if d.is_dir()]
        if subs:
            for d in subs:
                for p in d.glob("*.pdf"):
                    pdfs.append((str(p.resolve()), d.name))
        else:
            for p in root.glob("*.pdf"):
                pdfs.append((str(p.resolve()), root.name))

        if not pdfs:
            print("未发现 PDF 文件。")
            return

        iterator = enumerate(pdfs, 1)
        if logging_redirect_tqdm:
            with logging_redirect_tqdm():
                files_pbar = tqdm(total=len(pdfs), desc="批处理：文档处理", unit="file") if tqdm else None
                for i, (path, category) in iterator:
                    name = os.path.basename(path)
                    if tqdm:
                        tqdm.write(f"\n[{i}/{len(pdfs)}] {name}")
                    else:
                        print(f"\n[{i}/{len(pdfs)}] {name}")
                    rec = tracker.records.get(path)
                    total_pages = (rec and rec.total_pages) or _local_total_pages(path)
                    if total_pages <= 0:
                        total_pages = _ensure_total_pages(tracker, pipe, path, category)
                    if total_pages <= 0:
                        print("  [WARN] 未能获取总页数，跳过")
                        if files_pbar: files_pbar.update(1)
                        continue
                    done = (rec and rec.done_ranges) or []
                    pending_ranges = tracker._compute_pending(total_pages, done)
                    if not pending_ranges:
                        if tqdm:
                            tqdm.write("  已无缺口，跳过")
                        else:
                            print("  已无缺口，跳过")
                        if files_pbar: files_pbar.update(1)
                        continue
                    # 过滤指定页段
                    if page_range:
                        pr_s, pr_e = page_range
                        filtered: list[list[int]] = []
                        for s, e in pending_ranges:
                            overlap_s = max(s, pr_s)
                            overlap_e = min(e, pr_e)
                            if overlap_s <= overlap_e:
                                filtered.append([overlap_s, overlap_e])
                        pending_ranges = filtered
                        if not pending_ranges:
                            if tqdm:
                                tqdm.write("  指定页段无缺口，跳过")
                            else:
                                print("  指定页段无缺口，跳过")
                            if files_pbar: files_pbar.update(1)
                            continue
                    # 计算页数进度条
                    plan_pages = 0
                    for s, e in pending_ranges:
                        cur = s
                        while cur <= e:
                            end = min(cur + SPAN - 1, e)
                            plan_pages += max(0, min(end, total_pages) - max(1, cur) + 1)
                            cur = end + 1
                    pages_pbar = tqdm(total=plan_pages, desc="页段处理", unit="page") if tqdm else None
                    for s, e in pending_ranges:
                        cur = s
                        while cur <= e:
                            end = min(cur + SPAN - 1, e)
                            if tqdm:
                                tqdm.write(f"  [页段] {cur}-{end}")
                            else:
                                print(f"  [页段] {cur}-{end}")
                            _ = pipe.process_document(pdf_path=path, category=category, page_range=(cur, end))
                            if pages_pbar:
                                pages_pbar.update(max(0, min(end, total_pages) - max(1, cur) + 1))
                            cur = end + 1
                    if pages_pbar: pages_pbar.close()
                    if files_pbar: files_pbar.update(1)
                if files_pbar: files_pbar.close()
                else:
                    for i, (path, category) in iterator:
                        name = os.path.basename(path)
                        print(f"\n[{i}/{len(pdfs)}] {name}")
                        rec = tracker.records.get(path)
                        total_pages = (rec and rec.total_pages) or _local_total_pages(path)
                        if total_pages <= 0:
                            total_pages = _ensure_total_pages(tracker, pipe, path, category)
                        if total_pages <= 0:
                            print("  [WARN] 未能获取总页数，跳过")
                            continue
                        done = (rec and rec.done_ranges) or []
                        pending_ranges = tracker._compute_pending(total_pages, done)
                        if not pending_ranges:
                            print("  已无缺口，跳过")
                            continue
                        if page_range:
                            pr_s, pr_e = page_range
                            filtered: list[list[int]] = []
                            for s, e in pending_ranges:
                                overlap_s = max(s, pr_s)
                                overlap_e = min(e, pr_e)
                                if overlap_s <= overlap_e:
                                    filtered.append([overlap_s, overlap_e])
                            pending_ranges = filtered
                            if not pending_ranges:
                                print("  指定页段无缺口，跳过")
                                continue
                        for s, e in pending_ranges:
                            cur = s
                            while cur <= e:
                                end = min(cur + SPAN - 1, e)
                                print(f"  [页段] {cur}-{end}")
                                _ = pipe.process_document(pdf_path=path, category=category, page_range=(cur, end))
                                cur = end + 1

                print("\n批处理完成。")

                print("\n" + "=" * 70)
                tracker.print_report(plain=True)

    finally:
        
        pipe.close()

if __name__ == "__main__":
    main()