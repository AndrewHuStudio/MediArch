# script/benchmark_run.py
"""
MediArch Benchmark 批量测试脚本

读取 benchmark_scoring.csv 中的 18 道题，
分别以 R0/R1/R2 三种检索模式调用 API，
将真实系统回答写回 CSV。

用法:
    python script/benchmark_run.py                          # 跑全部 (R0+R1+R2)
    python script/benchmark_run.py --mode R0                # 只跑 R0
    python script/benchmark_run.py --mode R0 R1             # 跑 R0 和 R1
    python script/benchmark_run.py --ids Q01 Q05 Q12        # 只跑指定题目
    python script/benchmark_run.py --api http://x.x:8010    # 自定义 API 地址
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ---------- 路径 ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = PROJECT_ROOT / "docs" / "智能体检索实验" / "benchmark_scoring.csv"
DEFAULT_API = "http://localhost:8010"

# ---------- 列名映射 ----------
MODE_COL = {"R0": "R0_Answer", "R1": "R1_Answer", "R2": "R2_Answer"}


def call_chat_api(api_base: str, question: str, mode: str, timeout: int = 180) -> str:
    """调用 MediArch 非流式 chat API，返回回答文本。"""
    url = f"{api_base}/api/v1/chat"
    payload = json.dumps({
        "message": question,
        "retrieval_mode": mode,
        "stream": False,
        "include_citations": True,
        "include_diagnostics": False,
        "include_online_search": False,
    }).encode("utf-8")

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("message", "")
    except HTTPError as e:
        return f"[API ERROR {e.code}] {e.read().decode('utf-8', errors='replace')[:500]}"
    except URLError as e:
        return f"[CONNECTION ERROR] {e.reason}"
    except Exception as e:
        return f"[ERROR] {e}"


def load_csv(path: Path):
    """读取 CSV，返回 (headers, rows)，rows 为 list of dict。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        rows = list(reader)
    return headers, rows


def save_csv(path: Path, headers, rows):
    """写回 CSV。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="MediArch Benchmark Runner")
    parser.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default: {DEFAULT_API})")
    parser.add_argument("--mode", nargs="+", default=["R0", "R1", "R2"],
                        choices=["R0", "R1", "R2"], help="Retrieval modes to run")
    parser.add_argument("--ids", nargs="+", default=None, help="Only run specific question IDs (e.g. Q01 Q05)")
    parser.add_argument("--timeout", type=int, default=180, help="API timeout per request in seconds")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests in seconds")
    parser.add_argument("--csv", default=str(CSV_PATH), help="Path to benchmark CSV")
    parser.add_argument("--skip-existing", action="store_true", help="Skip questions that already have answers")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[FAIL] CSV not found: {csv_path}")
        sys.exit(1)

    headers, rows = load_csv(csv_path)
    modes = [m.upper() for m in args.mode]
    target_ids = set(i.upper() for i in args.ids) if args.ids else None

    # 统计
    total_tasks = 0
    skipped = 0
    success = 0
    failed = 0

    for mode in modes:
        col = MODE_COL[mode]
        for row in rows:
            qid = row["ID"]
            if target_ids and qid.upper() not in target_ids:
                continue
            if args.skip_existing and row.get(col, "").strip():
                skipped += 1
                continue
            total_tasks += 1

    print(f"=" * 60)
    print(f"MediArch Benchmark Runner")
    print(f"=" * 60)
    print(f"API:       {args.api}")
    print(f"CSV:       {csv_path}")
    print(f"Modes:     {modes}")
    print(f"Questions: {target_ids or 'ALL (18)'}")
    print(f"Tasks:     {total_tasks} (skip existing: {skipped})")
    print(f"Timeout:   {args.timeout}s per request")
    print(f"Delay:     {args.delay}s between requests")
    print(f"=" * 60)

    if total_tasks == 0:
        print("[OK] Nothing to do.")
        return

    current = 0
    for mode in modes:
        col = MODE_COL[mode]
        print(f"\n--- Mode: {mode} ---")

        for row in rows:
            qid = row["ID"]
            question = row["Question"]
            if target_ids and qid.upper() not in target_ids:
                continue
            if args.skip_existing and row.get(col, "").strip():
                continue

            current += 1
            print(f"[{current}/{total_tasks}] {qid} ({mode}) ...", end=" ", flush=True)

            t0 = time.time()
            answer = call_chat_api(args.api, question, mode, timeout=args.timeout)
            elapsed = time.time() - t0

            if answer.startswith("[") and ("ERROR" in answer[:30]):
                print(f"FAIL ({elapsed:.1f}s)")
                print(f"  -> {answer[:200]}")
                failed += 1
            else:
                print(f"OK ({elapsed:.1f}s, {len(answer)} chars)")
                success += 1

            row[col] = answer

            # 每题跑完立即保存，防止中途崩溃丢数据
            save_csv(csv_path, headers, rows)

            if args.delay > 0:
                time.sleep(args.delay)

    print(f"\n{'=' * 60}")
    print(f"Done! success={success}, failed={failed}, skipped={skipped}")
    print(f"Results saved to: {csv_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
