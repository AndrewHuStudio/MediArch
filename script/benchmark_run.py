# script/benchmark_run.py
"""
MediArch 54-question benchmark runner.

Reads the canonical question table, runs selected systems through the Chat API,
and writes one long-form row per (question_id, system_id). This format is
designed for model-based judging, inter-rater reliability, and bootstrap
confidence intervals.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from script import benchmark_pipeline


DEFAULT_API = "http://localhost:8010"

# Kept for compatibility with existing tests and old wide-table exports.
MODE_COL = {
    "R0": "R0_Answer",
    "R1": "R1_Answer",
    "R2": "R2_Answer",
    "BM25": "BM25_Answer",
    "VRAG": "VRAG_Answer",
}


def _normalize_id(value: str) -> str:
    value = (value or "").strip().upper()
    if value.startswith("Q") and value[1:].isdigit():
        return f"Q{int(value[1:]):03d}"
    return value


def _unique_join(values: list[str]) -> str:
    seen = set()
    output = []
    for value in values:
        value = (value or "").strip()
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return "; ".join(output)


def normalize_timeout(timeout: int | float | None) -> int | float | None:
    if timeout is None:
        return None
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return timeout
    if value <= 0:
        return None
    if value.is_integer():
        return int(value)
    return value


def build_chat_payload(question: str, mode: str, question_meta: dict | None = None) -> dict:
    payload = {
        "message": question,
        "retrieval_mode": mode,
        "stream": False,
        "include_citations": True,
        "include_diagnostics": True,
        "include_online_search": False,
        "max_citations": 30,
    }
    if question_meta:
        metadata = {
            key: str(question_meta.get(key) or "")
            for key in ("question_id", "source_type", "task_type", "difficulty")
            if question_meta.get(key)
        }
        if metadata:
            payload["metadata"] = metadata
    return payload


def call_chat_api(
    api_base: str,
    question: str,
    mode: str,
    timeout: int = 180,
    *,
    question_meta: dict | None = None,
) -> dict:
    """Call the non-streaming Chat API and return the raw JSON response or an error payload."""
    url = f"{api_base.rstrip('/')}/api/v1/chat"
    payload = json.dumps(
        build_chat_payload(question, mode, question_meta=question_meta),
        ensure_ascii=False,
    ).encode("utf-8")

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=normalize_timeout(timeout)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return {
            "message": "",
            "error": f"[API ERROR {exc.code}] {exc.read().decode('utf-8', errors='replace')[:500]}",
        }
    except URLError as exc:
        return {"message": "", "error": f"[CONNECTION ERROR] {exc.reason}"}
    except Exception as exc:  # pragma: no cover - defensive boundary around remote API
        return {"message": "", "error": f"[ERROR] {exc}"}


def extract_response_payload(payload: dict) -> dict[str, str]:
    citations = payload.get("citations") or []
    if not isinstance(citations, list):
        citations = []

    doc_ids = []
    chunk_ids = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        source = citation.get("source") or citation.get("document_title") or citation.get("document_id")
        chunk_id = citation.get("chunk_id") or citation.get("chunk") or citation.get("id")
        if source:
            doc_ids.append(str(source))
        if chunk_id:
            chunk_ids.append(str(chunk_id))

    return {
        "answer": str(payload.get("message") or ""),
        "citations": json.dumps(citations, ensure_ascii=False),
        "retrieved_doc_ids": _unique_join(doc_ids),
        "retrieved_chunk_ids": _unique_join(chunk_ids),
        "response_took_ms": str(payload.get("took_ms") or ""),
        "diagnostics": json.dumps(payload.get("diagnostics") or [], ensure_ascii=False),
        "error": str(payload.get("error") or ""),
    }


def update_run_row(row: dict[str, str], parsed: dict[str, str], *, latency_s: float) -> None:
    for key in (
        "answer",
        "citations",
        "retrieved_doc_ids",
        "retrieved_chunk_ids",
        "response_took_ms",
        "diagnostics",
        "error",
    ):
        row[key] = parsed.get(key, "")
    row["latency_s"] = f"{latency_s:.2f}"
    row["run_status"] = "error" if parsed.get("error") else "done"


def _load_questions(path: Path) -> dict[str, dict[str, str]]:
    questions = benchmark_pipeline.read_csv(path)
    benchmark_pipeline.validate_questions(questions)
    return {row["question_id"]: row for row in questions}


def _load_or_init_runs(questions: list[dict[str, str]], path: Path) -> list[dict[str, str]]:
    if path.exists():
        return benchmark_pipeline.read_csv(path)
    rows = benchmark_pipeline.build_run_matrix(questions)
    benchmark_pipeline.write_csv(path, benchmark_pipeline.RUN_FIELDS, rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="MediArch 54-question Benchmark Runner")
    parser.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default: {DEFAULT_API})")
    parser.add_argument(
        "--mode",
        nargs="+",
        default=[system_id for system_id, _ in benchmark_pipeline.SYSTEMS],
        choices=[system_id for system_id, _ in benchmark_pipeline.SYSTEMS],
        help="Retrieval modes to run",
    )
    parser.add_argument("--ids", nargs="+", default=None, help="Only run selected question IDs, e.g. Q001 Q005")
    parser.add_argument("--timeout", type=int, default=180, help="API timeout per request in seconds")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests in seconds")
    parser.add_argument("--questions", default=str(benchmark_pipeline.QUESTIONS_PATH), help="Canonical questions CSV")
    parser.add_argument("--runs", default=str(benchmark_pipeline.RUNS_PATH), help="Long-form run output CSV")
    parser.add_argument("--csv", default=None, help="Deprecated alias for --runs")
    parser.add_argument("--skip-existing", action="store_true", help="Skip rows that already have an answer")
    args = parser.parse_args()

    questions_path = Path(args.questions)
    runs_path = Path(args.csv or args.runs)
    if not questions_path.exists():
        print(f"[FAIL] Questions CSV not found: {questions_path}")
        sys.exit(1)

    questions_by_id = _load_questions(questions_path)
    questions = list(questions_by_id.values())
    runs = _load_or_init_runs(questions, runs_path)

    modes = {mode.upper() for mode in args.mode}
    target_ids = {_normalize_id(qid) for qid in args.ids} if args.ids else None

    targets = [
        row
        for row in runs
        if row.get("system_id", "").upper() in modes
        and (target_ids is None or _normalize_id(row.get("question_id", "")) in target_ids)
        and not (args.skip_existing and row.get("answer", "").strip())
    ]

    print("=" * 60)
    print("MediArch Benchmark Runner")
    print("=" * 60)
    print(f"API:       {args.api}")
    print(f"Questions: {questions_path}")
    print(f"Runs:      {runs_path}")
    print(f"Modes:     {sorted(modes)}")
    print(f"Questions: {sorted(target_ids) if target_ids else 'ALL (54)'}")
    print(f"Tasks:     {len(targets)}")
    display_timeout = normalize_timeout(args.timeout)
    print(f"Timeout:   {display_timeout if display_timeout is not None else 'unlimited'} per request")
    print("=" * 60)

    if not targets:
        print("[OK] Nothing to do.")
        return

    success = 0
    failed = 0
    for index, row in enumerate(targets, start=1):
        qid = _normalize_id(row.get("question_id", ""))
        mode = row.get("system_id", "").upper()
        question_row = questions_by_id[qid]
        question = question_row["question"]
        question_meta = {
            "question_id": qid,
            "source_type": question_row.get("source_type", ""),
            "task_type": question_row.get("task_type", ""),
            "difficulty": question_row.get("difficulty", ""),
        }
        print(f"[{index}/{len(targets)}] {qid} ({mode}) ...", end=" ", flush=True)

        started = time.time()
        response = call_chat_api(
            args.api,
            question,
            mode,
            timeout=args.timeout,
            question_meta=question_meta,
        )
        elapsed = time.time() - started
        parsed = extract_response_payload(response)
        update_run_row(row, parsed, latency_s=elapsed)

        benchmark_pipeline.write_csv(runs_path, benchmark_pipeline.RUN_FIELDS, runs)

        if row["run_status"] == "done":
            print(f"OK ({elapsed:.1f}s, {len(row['answer'])} chars)")
            success += 1
        else:
            print(f"FAIL ({elapsed:.1f}s)")
            print(f"  -> {row['error'][:200]}")
            failed += 1

        if args.delay > 0:
            time.sleep(args.delay)

    print("=" * 60)
    print(f"Done! success={success}, failed={failed}")
    print(f"Results saved to: {runs_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
