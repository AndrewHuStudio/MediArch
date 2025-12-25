
import sys
import json
import os
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from dotenv import load_dotenv

# 添加项目根目录到 sys.path（用于 CLI 直接运行）
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


@dataclass
class DocumentScanRecord:
    """单个文档的扫描记录"""
    file_path: str
    file_name: str
    category: str  # 标准规范/政策文件/参考论文/书籍报告
    total_pages: int
    scanned_pages: int
    status: str  # pending/processing/completed/failed
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    error_message: Optional[str] = None
    mongo_doc_id: Optional[str] = None
    total_chunks: int = 0
    done_ranges: List[List[int]] = field(default_factory=list)
    last_ocr_at: Optional[str] = None
    engine: Optional[str] = None


class OCRProgressTracker:
    """OCR进度追踪器"""
    
    def __init__(self, progress_file: str = "backend/databases/ingestion/ocr_progress.json"):
        self.progress_file = Path(progress_file)
        self.records: Dict[str, DocumentScanRecord] = {}
        self._load_progress()

    # 路径规范化：使用绝对路径，统一分隔符
    @staticmethod
    def _norm_path(p: str) -> str:
        try:
            return str(Path(p).resolve())
        except Exception:
            return p.replace("\\", "/")
    
    def _load_progress(self):
        """从文件加载进度"""
        if self.progress_file.exists():
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                migrated: Dict[str, DocumentScanRecord] = {}
                for key, record_dict in data.items():
                    # 迁移：规范 key 与 file_path
                    norm_key = self._norm_path(key)
                    record_dict['file_path'] = self._norm_path(record_dict.get('file_path', norm_key))
                    # 兼容旧账本：done_ranges 不存在时初始化
                    if 'done_ranges' not in record_dict:
                        scanned = int(record_dict.get('scanned_pages') or 0)
                        if scanned > 0:
                            record_dict['done_ranges'] = [[1, scanned]]
                            if record_dict.get('status') == 'processing':
                                record_dict['status'] = 'partial'
                        else:
                            record_dict['done_ranges'] = []
                    rec = DocumentScanRecord(**record_dict)
                    # 合并可能因路径分隔符不同造成的重复项
                    if norm_key in migrated:
                        old = migrated[norm_key]
                        # 合并 done_ranges
                        ranges = (old.done_ranges or []) + (rec.done_ranges or [])
                        merged = self._merge_ranges(ranges)
                        old.done_ranges = merged
                        # 取较大 scanned_pages
                        old.scanned_pages = max(old.scanned_pages, rec.scanned_pages)
                        # total_pages 取较大值
                        tp = max(old.total_pages or -1, rec.total_pages or -1)
                        old.total_pages = tp
                        # 状态：若任一为 completed 则 completed，否则 partial/processing/failed 以优先级合并
                        if old.status == 'completed' or rec.status == 'completed':
                            old.status = 'completed'
                        elif 'failed' in (old.status, rec.status):
                            old.status = 'failed'
                        else:
                            old.status = 'partial'
                        migrated[norm_key] = old
                    else:
                        migrated[norm_key] = rec
                self.records = migrated
    
    def _save_progress(self):
        """保存进度到文件"""
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            data = {key: asdict(record) for key, record in self.records.items()}
            json.dump(data, f, ensure_ascii=False, indent=2)

    # 区间工具
    @staticmethod
    def _normalize_range(s: int, e: int) -> Tuple[int, int]:
        s = int(s); e = int(e)
        if s <= 0 or e <= 0 or e < s:
            raise ValueError("页段不合法：start/end 必须为正整数且 end>=start")
        return s, e

    @staticmethod
    def _merge_ranges(ranges: List[List[int]]) -> List[List[int]]:
        if not ranges:
            return []
        rs = sorted((int(a), int(b)) for a, b in ranges)
        merged: List[List[int]] = []
        cs, ce = rs[0]
        for s, e in rs[1:]:
            if s <= ce + 1:
                ce = max(ce, e)
            else:
                merged.append([cs, ce])
                cs, ce = s, e
        merged.append([cs, ce])
        return merged

    @staticmethod
    def _compute_pending(total_pages: int, done_ranges: List[List[int]]) -> List[List[int]]:
        if not total_pages or total_pages <= 0:
            return []
        merged = OCRProgressTracker._merge_ranges(done_ranges or [])
        pending: List[List[int]] = []
        cur = 1
        for s, e in merged:
            if cur < s:
                pending.append([cur, s - 1])
            cur = e + 1
        if cur <= total_pages:
            pending.append([cur, total_pages])
        return pending

    # 合并页段到账本
    def merge_done_range(self, file_path: str, category: str, start: int, end: int):
        s, e = self._normalize_range(start, end)
        key = self._norm_path(file_path)
        if key not in self.records:
            self.records[key] = DocumentScanRecord(
                file_path=key,
                file_name=os.path.basename(key),
                category=category,
                total_pages=-1,
                scanned_pages=0,
                status='partial',
            )
        rec = self.records[key]
        rs = rec.done_ranges or []
        rs.append([s, e])
        rec.done_ranges = self._merge_ranges(rs)
        # 更新已扫页数
        scanned = 0
        for a, b in rec.done_ranges:
            scanned += (b - a + 1)
        rec.scanned_pages = scanned
        # 根据总页数更新状态
        if rec.total_pages and rec.total_pages > 0:
            pending = self._compute_pending(rec.total_pages, rec.done_ranges)
            rec.status = 'completed' if not pending else 'partial'
        else:
            rec.status = 'partial'
        rec.last_ocr_at = datetime.now().isoformat()
        self._save_progress()
    
    def start_document(self, file_path: str, category: str, total_pages: int) -> str:
        """开始扫描文档"""
        file_path = self._norm_path(file_path)
        file_name = os.path.basename(file_path)
        doc_key = file_path
        
        record = DocumentScanRecord(
            file_path=file_path,
            file_name=file_name,
            category=category,
            total_pages=total_pages,
            scanned_pages=0,
            status="processing",
            start_time=datetime.now().isoformat()
        )
        
        self.records[doc_key] = record
        self._save_progress()
        return doc_key
    
    def update_progress(self, doc_key: str, scanned_pages: int, total_chunks: int = 0):
        """更新扫描进度"""
        if doc_key in self.records:
            self.records[doc_key].scanned_pages = scanned_pages
            self.records[doc_key].total_chunks = total_chunks
            self._save_progress()
    
    def complete_document(self, doc_key: str, mongo_doc_id: str, total_chunks: int):
        """完成文档扫描"""
        if doc_key in self.records:
            self.records[doc_key].status = "completed"
            self.records[doc_key].end_time = datetime.now().isoformat()
            self.records[doc_key].mongo_doc_id = mongo_doc_id
            self.records[doc_key].total_chunks = total_chunks
            self._save_progress()
    
    def fail_document(self, doc_key: str, error_message: str):
        """标记文档扫描失败"""
        if doc_key in self.records:
            self.records[doc_key].status = "failed"
            self.records[doc_key].end_time = datetime.now().isoformat()
            self.records[doc_key].error_message = error_message
            self._save_progress()
    
    def is_document_scanned(self, file_path: str) -> bool:
        """检查文档是否已扫描"""
        file_path = self._norm_path(file_path)
        return file_path in self.records and self.records[file_path].status == "completed"
    
    def get_category_stats(self) -> Dict[str, Dict]:
        """获取各类别的统计信息"""
        stats = {}
        categories = set(record.category for record in self.records.values())
        
        for category in categories:
            category_records = [r for r in self.records.values() if r.category == category]
            stats[category] = {
                "total": len(category_records),
                "completed": len([r for r in category_records if r.status == "completed"]),
                "processing": len([r for r in category_records if r.status == "processing"]),
                "failed": len([r for r in category_records if r.status == "failed"]),
                "total_pages": sum(r.total_pages for r in category_records),
                "scanned_pages": sum(r.scanned_pages for r in category_records),
                "total_chunks": sum(r.total_chunks for r in category_records if r.status == "completed")
            }
        
        return stats
    
    def get_overall_stats(self) -> Dict:
        """获取总体统计信息"""
        all_records = list(self.records.values())
        return {
            "total_documents": len(all_records),
            "completed": len([r for r in all_records if r.status == "completed"]),
            "processing": len([r for r in all_records if r.status == "processing"]),
            "failed": len([r for r in all_records if r.status == "failed"]),
            "total_pages": sum(r.total_pages for r in all_records),
            "scanned_pages": sum(r.scanned_pages for r in all_records),
            "total_chunks": sum(r.total_chunks for r in all_records if r.status == "completed")
        }
    
    def print_report(self, plain: bool = False):
        """打印进度报告；plain=True 使用纯文本图标，避免控制台编码问题。"""
        icon = {
            "title": "OCR 扫描进度报告" if plain else "📊 OCR 扫描进度报告",
            "ok": "OK" if plain else "✅",
            "run": "RUN" if plain else "⏳",
            "fail": "FAIL" if plain else "❌",
            "cat": "-" if plain else "📁",
            "pend": "PENDING" if plain else "⏸️",
        }

        print("\n" + "="*60)
        print(icon["title"])
        print("="*60)

        # 总体统计
        overall = self.get_overall_stats()
        print(f"\n【总体进度】")
        print(f"  文档总数: {overall['total_documents']}")
        print(f"  已完成: {overall['completed']} {icon['ok']}")
        print(f"  处理中: {overall['processing']} {icon['run']}")
        print(f"  失败: {overall['failed']} {icon['fail']}")
        print(f"  总页数: {overall['total_pages']}")
        print(f"  已扫描: {overall['scanned_pages']}")
        print(f"  总Chunks: {overall['total_chunks']}")

        # 分类统计
        category_stats = self.get_category_stats()
        print(f"\n【分类进度】")
        for category, stats in category_stats.items():
            completion_rate = (stats['completed'] / stats['total'] * 100) if stats['total'] > 0 else 0
            print(f"\n  {icon['cat']} {category}")
            print(f"     文档: {stats['completed']}/{stats['total']} ({completion_rate:.1f}%)")
            print(f"     页面: {stats['scanned_pages']}/{stats['total_pages']}")
            print(f"     Chunks: {stats['total_chunks']}")

        # 详细文档列表（分组且去重显示）
        print(f"\n【文档详情】")
        for category in sorted(category_stats.keys()):
            # 以规范化路径去重
            uniq: Dict[str, DocumentScanRecord] = {}
            for r in self.records.values():
                if r.category == category:
                    k = self._norm_path(r.file_path)
                    if k not in uniq:
                        uniq[k] = r
                    else:
                        # 同一文件名的重复记录合并（done_ranges 最大化）
                        merged = self._merge_ranges((uniq[k].done_ranges or []) + (r.done_ranges or []))
                        uniq[k].done_ranges = merged
                        uniq[k].scanned_pages = max(uniq[k].scanned_pages, r.scanned_pages)
                        uniq[k].total_pages = max(uniq[k].total_pages or -1, r.total_pages or -1)
                        if uniq[k].status != 'completed' and r.status == 'completed':
                            uniq[k].status = 'completed'
            records = list(uniq.values())
            if records:
                print(f"\n  {icon['cat']} {category}:")
                for record in records:
                    status_icon = {
                        "completed": icon["ok"],
                        "processing": icon["run"],
                        "failed": icon["fail"],
                        "pending": icon["pend"],
                    }.get(record.status, "?")
                    print(f"     {status_icon} {record.file_name}")
                    print(f"        页数: {record.scanned_pages}/{record.total_pages}, Chunks: {record.total_chunks}")
                    pending = self._compute_pending(record.total_pages, record.done_ranges)
                    print(f"        待完成: {pending}")
                    if record.status == "failed" and record.error_message:
                        print(f"        错误: {record.error_message}")

        print("\n" + "="*60 + "\n")
    
    # backend/databases/ingestion/ocr/ocr_progress_tracker.py
    def get_pending_documents(self, documents_dir: str = "backend/databases/documents") -> List[Dict]:
        documents_path = Path(documents_dir).resolve()
        pending = []
        def add_pdf_dir(cat_dir: Path, category: str):
            for pdf_file in cat_dir.glob("*.pdf"):
                    try:
                        file_path = str(pdf_file.resolve().relative_to(Path.cwd().resolve()))
                    except ValueError:
                        file_path = str(pdf_file.resolve())
                    if not self.is_document_scanned(file_path):
                        pending.append({"file_path": file_path, "file_name": pdf_file.name, "category": category})

        # 情况一：传入根目录，遍历其子目录
        subdirs = [d for d in documents_path.iterdir() if d.is_dir()]
        if subdirs:
            for cat_dir in subdirs:
                add_pdf_dir(cat_dir, cat_dir.name)
        else:
            # 情况二：传入叶子目录，直接在当前目录找 PDF
            add_pdf_dir(documents_path, documents_path.name)
        return pending

    def prune_missing(self) -> int:
        """删除记录中已不存在的文件条目，返回删除数量。"""
        to_del = [k for k, r in self.records.items() if not Path(r.file_path).exists()]
        for k in to_del:
            self.records.pop(k, None)
        if to_del:
            self._save_progress()
        return len(to_del)

    def reset(self, file_path: Optional[str] = None, all_clear: bool = False) -> int:
        """重置进度账本；删除指定文件条目或清空全部．返回删除数量。"""
        if all_clear:
            n = len(self.records)
            self.records.clear()
            self._save_progress()
            return n
        if file_path and file_path in self.records:
            self.records.pop(file_path, None)
            self._save_progress()
            return 1
        return 0

    def set_status(self, file_path: str, status: str):
        """手工设置某文件状态，支持 completed/failed/processing/pending。"""
        if file_path in self.records:
            self.records[file_path].status = status
            if status == "completed":
                self.records[file_path].end_time = datetime.now().isoformat()
            self._save_progress()


# 便捷函数
def get_tracker() -> OCRProgressTracker:
    """获取全局追踪器实例"""
    return OCRProgressTracker()


def _reconfigure_stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
        sys.stderr.reconfigure(encoding="utf-8", errors="ignore")
    except Exception:
        pass

def _friendly_overview():
    """无参数一键概览：自动扫描 documents 目录并结合账本展示“已完成/待完成”。"""
    _reconfigure_stdout_utf8()
    tracker = get_tracker()
    root = Path("backend/databases/documents").resolve()
    # 可选：准备 OCR 轻量探测器（仅在无法本地获取总页数时使用）
    load_dotenv()
    _ocr_probe = None
    try:
        from backend.databases.ingestion.ocr.textin_client import TextInClient as _TC
        _ocr_probe = _TC()
    except Exception:
        _ocr_probe = None
    # 收集 PDF（绝对路径）
    pdfs: List[Tuple[str, str]] = []
    subs = [d for d in root.iterdir() if d.is_dir()]
    if subs:
        for d in subs:
            for p in d.glob("*.pdf"):
                pdfs.append((str(p.resolve()), d.name))
    else:
        for p in root.glob("*.pdf"):
            pdfs.append((str(p.resolve()), root.name))

    # 先尽力为每个文件补齐 total_pages（优先本地读取，其次 OCR 轻探测）
    def _local_total_pages(pdf: str) -> int:
        try:
            try:
                from pypdf import PdfReader  # 优先使用 pypdf
                return len(PdfReader(pdf).pages)
            except Exception:
                pass
            try:
                from PyPDF2 import PdfReader  # 兼容旧库名
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

    dirty = False
    for abs_pdf, category in pdfs:
        rec = tracker.records.get(abs_pdf)
        current_tp = (rec and rec.total_pages) or -1
        # 本地优先：强制重新读取并覆盖账本中可能错误的总页数
        tp_local = _local_total_pages(abs_pdf)
        if tp_local > 0:
            if rec is None:
                tracker.records[abs_pdf] = DocumentScanRecord(
                    file_path=abs_pdf,
                    file_name=Path(abs_pdf).name,
                    category=category,
                    total_pages=tp_local,
                    scanned_pages=0,
                    status='partial',
                )
                dirty = True
            elif current_tp != tp_local:
                # 只要不一致就更新（包括账本里写错的情况）
                tracker.records[abs_pdf].total_pages = tp_local
                dirty = True
        elif (rec is None or not current_tp or current_tp <= 0) and _ocr_probe:
            # 退而求其次：OCR 轻探测 1 页拿 total_page_number
            try:
                probe = _ocr_probe.parse_pdf(abs_pdf, page_range=(1, 1))
                tp = int((probe.get("result", {}) or {}).get("total_page_number") or -1)
                if tp > 0:
                    if rec is None:
                        tracker.records[abs_pdf] = DocumentScanRecord(
                            file_path=abs_pdf,
                            file_name=Path(abs_pdf).name,
                            category=category,
                            total_pages=tp,
                            scanned_pages=0,
                            status='partial',
                        )
                    else:
                        tracker.records[abs_pdf].total_pages = tp
                    dirty = True
            except Exception:
                pass

    if dirty:
        tracker._save_progress()

    # 汇总
    print("\n" + "="*60)
    print("OCR 进度总览（基于账本）")
    print("="*60 + "\n")
    total_files = len(pdfs)
    completed = partial = new = failed = 0
    for abs_pdf, category in pdfs:
        rec = tracker.records.get(abs_pdf)
        if not rec:
            new += 1
            continue
        if rec.status == 'completed':
            completed += 1
        elif rec.status == 'failed':
            failed += 1
        else:
            partial += 1
    print(f"文件总数: {total_files}")
    print(f"已完成: {completed} | 进行中: {partial} | 新增: {new} | 失败: {failed}\n")

    # 逐类展示
    from collections import defaultdict
    buckets: Dict[str, List[Tuple[str, DocumentScanRecord]]] = defaultdict(list)
    for abs_pdf, category in pdfs:
        buckets[category].append((abs_pdf, tracker.records.get(abs_pdf)))

    for cat in sorted(buckets.keys()):
        print(f"[{cat}]")
        for abs_pdf, rec in buckets[cat]:
            name = Path(abs_pdf).name
            if not rec:
                total_local = _local_total_pages(abs_pdf)
                if total_local <= 0:
                    print(f"  NEW   {name}  完成: 0/?   待完成: ?/?")
                else:
                    print(f"  NEW   {name}  完成: 0/{total_local}  待完成: {total_local}/{total_local}")
                continue
            # 以 done_ranges 重新计算已扫页数
            scanned = sum((b - a + 1) for a, b in (rec.done_ranges or []))
            scanned = max(scanned, rec.scanned_pages or 0)
            total = rec.total_pages if rec.total_pages and rec.total_pages > 0 else None
            if total:
                pending = max(total - scanned, 0)
                ratio_done = f"{scanned}/{total}"
                ratio_pending = f"{pending}/{total}"
            else:
                ratio_done = f"{scanned}/?"
                ratio_pending = "?/?"
            st = rec.status.upper()
            engine = getattr(rec, 'engine', None) or '-'
            print(f"  {st:6s} {name}  完成: {ratio_done}  待完成: {ratio_pending}  来源:{engine}")
        print("")


if __name__ == "__main__":
    _friendly_overview()
