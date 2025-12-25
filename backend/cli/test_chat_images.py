"""
Chat 出图快速回归脚本（CLI）

目标：替代 PowerShell 环脚本，提供 `python -m` 方式快速验证：
- /api/v1/chat 是否能返回 images[]
- message 末尾是否包含 [image:n] 占位符
- citations 是否包含 image_url / content_type=image

用法：
  python -m backend.cli.test_chat_images --message "请返回负压病房平面图（带图）并说明流线"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List


def _ensure_utf8_stdio() -> None:
    """Best-effort: avoid Windows GBK console crashing on Unicode output (e.g. VLM bullets like '▶')."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 60.0) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = raw.decode("utf-8", errors="replace") if raw else str(exc)
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {detail[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc


def _print_section(title: str) -> None:
    sys.stdout.write(f"\n=== {title} ===\n")


def main(argv: List[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="MediArch chat images probe")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base url")
    parser.add_argument("--top-k", type=int, default=8, help="top_k for retrieval")
    parser.add_argument("--message", required=True, help="user message")
    parser.add_argument("--doc-id", action="append", default=[], help="restrict retrieval to doc_id (repeatable)")
    parser.add_argument("--source-document", action="append", default=[], help="restrict retrieval to source_document (repeatable)")
    parser.add_argument("--page", action="append", type=int, default=[], help="prefer/limit retrieval near page number (repeatable)")
    parser.add_argument("--page-window", type=int, default=0, help="page window for --page (default 0=exact)")
    parser.add_argument("--timeout", type=float, default=60.0, help="http timeout seconds")
    parser.add_argument("--show-citations", type=int, default=5, help="print first N citations")
    parser.add_argument("--max-citations", type=int, default=10, help="max citations returned by API (default 10)")
    parser.add_argument("--show-head", type=int, default=0, help="print first N chars of message")
    parser.add_argument("--show-diagnostics", action="store_true", help="print diagnostics")
    parser.add_argument("--only-image-citations", action="store_true", help="only print image citations")
    parser.add_argument("--no-citations", action="store_true", help="disable citations in response")
    parser.add_argument("--no-diagnostics", action="store_true", help="disable diagnostics in response")
    args = parser.parse_args(argv)

    base_url = str(args.base_url).rstrip("/")
    uri = f"{base_url}/api/v1/chat"

    body = {
        "message": args.message,
        "top_k": int(args.top_k),
        "include_citations": not args.no_citations,
        "include_diagnostics": not args.no_diagnostics,
    }
    try:
        body["max_citations"] = max(1, min(int(args.max_citations or 10), 100))
    except Exception:
        body["max_citations"] = 10

    filters: Dict[str, Any] = {}
    if args.doc_id:
        cleaned = [str(v).strip() for v in args.doc_id if str(v).strip()]
        if cleaned:
            filters["doc_ids"] = cleaned
    if args.source_document:
        cleaned = [str(v).strip() for v in args.source_document if str(v).strip()]
        if cleaned:
            filters["source_documents"] = cleaned
    if args.page:
        cleaned_pages = []
        for p in args.page:
            try:
                p_int = int(p)
            except Exception:
                continue
            if p_int > 0:
                cleaned_pages.append(p_int)
        if cleaned_pages:
            filters["page_numbers"] = list(dict.fromkeys(cleaned_pages).keys())
            try:
                page_window = int(args.page_window or 0)
            except Exception:
                page_window = 0
            filters["page_window"] = max(0, min(page_window, 10))
    if filters:
        body["filters"] = filters

    sys.stdout.write(f"POST {uri}\n")
    sys.stdout.write(f"message: {args.message}\n")
    if filters:
        sys.stdout.write(f"filters: {json.dumps(filters, ensure_ascii=False)}\n")
    sys.stdout.flush()

    resp = _post_json(uri, body, timeout_sec=float(args.timeout))

    images = resp.get("images") or []
    if not isinstance(images, list):
        images = []
    message = str(resp.get("message") or "")
    citations = resp.get("citations") or []
    if not isinstance(citations, list):
        citations = []
    diagnostics = resp.get("diagnostics") or []

    _print_section("images[]")
    for img in images:
        sys.stdout.write(f"{img}\n")

    if args.show_diagnostics:
        _print_section("diagnostics")
        sys.stdout.write(json.dumps(diagnostics, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")

    if int(args.show_head or 0) > 0:
        _print_section("message (head)")
        head_n = int(args.show_head)
        head = message[:head_n]
        sys.stdout.write(f"{head}\n")
    _print_section("message (tail)")
    tail = message[-500:] if len(message) > 500 else message
    sys.stdout.write(f"{tail}\n")

    _print_section("citations (first 5)")
    shown = citations
    if args.only_image_citations:
        shown = [c for c in citations if isinstance(c, dict) and (c.get("content_type") == "image" or c.get("image_url"))]
    n = int(max(0, args.show_citations))
    sys.stdout.write(json.dumps(shown[:n], ensure_ascii=False, indent=2))
    sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n[WARN] interrupted\n")
        raise SystemExit(130)
    except Exception as exc:
        sys.stderr.write(f"\n[FAIL] {exc}\n")
        raise SystemExit(1)
