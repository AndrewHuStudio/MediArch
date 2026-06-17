"""Focused live R2-vs-VRAG claim-grounding probe.

Runs a few benchmark questions through both retrieval modes against the live
Chat API, then re-audits each returned answer with the SAME claim-level audit
the gate uses. Reports per-answer claim grounding (total / supported /
unsupported) so we can see whether the splitter+gate fix actually lowers
unsupported claims for R2 without collapsing answer content.

This is a measurement probe, not a unit test. It needs the live API on 8010.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
import urllib.error

from script import benchmark_pipeline as p
from backend.app.agents.evidence_orchestration import audit_claim_support

API = "http://localhost:8010/api/v1/chat"
QUESTION_IDS = ["Q006", "Q017", "Q024", "Q050"]
MODES = ["R2", "VRAG"]


def load_questions() -> dict[str, str]:
    rows = list(csv.DictReader(open(str(p.QUESTIONS_PATH), encoding="utf-8-sig")))
    return {r["question_id"]: r["question"] for r in rows}


def call(message: str, mode: str) -> dict:
    body = json.dumps(
        {
            "message": message,
            "retrieval_mode": mode,
            "stream": False,
            "include_citations": True,
            "include_diagnostics": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.load(r)
    d["_wall_s"] = round(time.time() - t, 1)
    return d


def _find_gate_diag(payload: dict) -> dict:
    """Locate synthesizer claim-gate diagnostics anywhere in the response."""
    blob = json.dumps(payload, ensure_ascii=False)
    if "claim_support_gate_applied" not in blob:
        return {}

    found: dict = {}

    def walk(obj):
        if isinstance(obj, dict):
            if "claim_support_gate_applied" in obj:
                found.update(obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(payload)
    pre = found.get("pre_gate_claim_support_audit") or {}
    post = found.get("claim_support_audit") or {}
    return {
        "gate_applied": found.get("claim_support_gate_applied"),
        "pre_unsupported": pre.get("unsupported_claim_count"),
        "pre_total_claims": len(pre.get("bindings", []) or []),
        "pre_passed": pre.get("passed"),
        "post_unsupported": post.get("unsupported_claim_count"),
        "post_passed": post.get("passed"),
    }


def grounding(answer: str, citations: list) -> dict:
    audit = audit_claim_support(answer or "", citations or [])
    total = len(audit.bindings)
    supported = sum(1 for b in audit.bindings if b.supported)
    cited = sum(1 for b in audit.bindings if b.citation_ids)
    return {
        "total_claims": total,
        "cited_claims": cited,
        "supported_claims": supported,
        "unsupported_claims": audit.unsupported_claim_count,
        "grounded_ratio": round(supported / total, 3) if total else 0.0,
        "passed": audit.passed,
    }


def main() -> None:
    questions = load_questions()
    results = []
    for qid in QUESTION_IDS:
        q = questions.get(qid, "")
        for mode in MODES:
            try:
                resp = call(q, mode)
                answer = resp.get("message") or ""
                cites = resp.get("citations") or []
                g = grounding(answer, cites)
                row = {
                    "qid": qid,
                    "mode": mode,
                    "wall_s": resp.get("_wall_s"),
                    "answer_chars": len(answer),
                    "citations": len(cites),
                    **g,
                    "gate": _find_gate_diag(resp),
                    "error": resp.get("error"),
                }
            except Exception as e:  # noqa: BLE001
                row = {"qid": qid, "mode": mode, "error": f"{type(e).__name__}: {e}"}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False))

    print("\n=== SUMMARY (by mode) ===")
    for mode in MODES:
        rows = [r for r in results if r["mode"] == mode and not r.get("error")]
        if not rows:
            print(f"{mode}: no successful rows")
            continue
        n = len(rows)
        avg = lambda k: round(sum(r[k] for r in rows) / n, 3)  # noqa: E731
        print(
            f"{mode}: n={n} "
            f"avg_grounded_ratio={avg('grounded_ratio')} "
            f"avg_unsupported={avg('unsupported_claims')} "
            f"avg_supported={avg('supported_claims')} "
            f"avg_answer_chars={avg('answer_chars')} "
            f"avg_wall_s={avg('wall_s')}"
        )


if __name__ == "__main__":
    main()
