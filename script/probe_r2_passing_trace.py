"""R2 链路三段快照探针：worker 召回 -> final_citations，判定召回 vs 传递丢失。

复用 probe_r2_vrag_grounding 的 live 调用约定。本脚本本轮不运行，
等用户允许后对 evidence_hit=0 的题取证。
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from typing import Any

from script import benchmark_pipeline as p

API = "http://localhost:8010/api/v1/chat"
# 默认目标：6-15 记忆里 evidence_hit=0 的学术/标准题
TARGETS = {
    "Q004": "GB 51039",
    "Q005": "GB",
    "Q024": "",
    "Q029": "",
    "Q033": "",
    "Q037": "",
}


def extract_source_trace(resp: dict[str, Any], gold_keyword: str) -> dict[str, Any]:
    """从 live 响应抽三段 source，判定金标准是召回缺失还是传递丢失。

    worker_recall: 各 worker top-k 命中的 source 列表（需后端在 diagnostics 暴露）。
    final: 最终 final_citations / citations 的 source 列表。
    """
    kw = (gold_keyword or "").strip().lower()
    diag = resp.get("diagnostics") or {}
    recall_map = diag.get("worker_recall") or {}
    recalled_sources = [s for sources in recall_map.values() for s in (sources or [])]
    final_sources = [str(c.get("source") or "") for c in (resp.get("citations") or [])]

    def _hit(sources: list[str]) -> bool:
        return any(kw and kw in str(s).lower() for s in sources)

    recalled = _hit(recalled_sources)
    in_final = _hit(final_sources)
    if not kw:
        verdict = "no_gold_keyword"
    elif recalled and in_final:
        verdict = "ok"
    elif recalled and not in_final:
        verdict = "passing_loss"
    else:
        verdict = "recall_miss"
    return {
        "recalled": recalled,
        "in_final": in_final,
        "verdict": verdict,
        "recalled_sources": recalled_sources,
        "final_sources": final_sources,
    }


def _call(message: str) -> dict:
    body = json.dumps(
        {
            "message": message,
            "retrieval_mode": "R2",
            "stream": False,
            "include_citations": True,
            "include_diagnostics": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def main() -> None:
    rows = list(csv.DictReader(open(str(p.QUESTIONS_PATH), encoding="utf-8-sig")))
    qmap = {r["question_id"]: r["question"] for r in rows}
    for qid, kw in TARGETS.items():
        q = qmap.get(qid, "")
        if not q:
            print(f"[SKIP] {qid} not in question table")
            continue
        try:
            resp = _call(q)
            trace = extract_source_trace(resp, kw)
            print(f"[{qid}] verdict={trace['verdict']} recalled={trace['recalled']} in_final={trace['in_final']}")
            print(f"       recall={trace['recalled_sources']}")
            print(f"       final={trace['final_sources']}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {qid}: {type(e).__name__}: {e}")
        time.sleep(1)


if __name__ == "__main__":
    main()
