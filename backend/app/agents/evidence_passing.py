"""R2 证据传递层：把 worker 证据转成干净、完整、低噪的 prompt 证据包。

从 result_synthesizer_agent 抽出，单一职责、纯函数、可独立单测。
约束：本模块 <=600 行(无注释)，函数 <=80 行。
"""
from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from backend.app.agents.evidence_orchestration import (
    EVIDENCE_LANES,
    build_evidence_ledger,
    build_evidence_tiers,
    classify_source_role,
)

EVIDENCE_TIER_KEYS = EVIDENCE_LANES

# 喂进 LLM 的正文 snippet 字符预算。
# 普通文档与 VRAG baseline(240 字)对齐，避免 R2 自己检索到的正文反而被砍得更短；
# code_spec 规范条文给更高预算，保证规范要点完整进入 prompt。
PROMPT_SNIPPET_CHARS = 240
PROMPT_SNIPPET_CHARS_CODE_SPEC = 400


def _citation_to_dict(cite: Any) -> Dict[str, Any]:
    if cite is None:
        return {}
    if isinstance(cite, dict):
        return {k: v for k, v in cite.items()}
    if hasattr(cite, "model_dump"):
        try:
            return cite.model_dump()
        except Exception:
            pass
    return {
        "source": getattr(cite, "source", ""),
        "location": getattr(cite, "location", ""),
        "snippet": getattr(cite, "snippet", ""),
        "chunk_id": getattr(cite, "chunk_id", None),
        "page_number": getattr(cite, "page_number", None),
        "section": getattr(cite, "section", None),
        "metadata": getattr(cite, "metadata", None),
        "positions": getattr(cite, "positions", None),
        "image_url": getattr(cite, "image_url", None),
        "content_type": getattr(cite, "content_type", None),
        "doc_id": getattr(cite, "doc_id", None),
    }


def _build_evidence_tiers(citations: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """按资料角色组织 citations，避免所有证据在 prompt 中变成平面列表。"""
    normalized = []
    for citation in citations or []:
        data = _citation_to_dict(citation)
        if data:
            normalized.append(data)
    return build_evidence_tiers(normalized)


def _serialize_evidence_ledger(citations: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = []
    for citation in citations or []:
        data = _citation_to_dict(citation)
        if data:
            normalized.append(data)
    ledger = build_evidence_ledger(normalized)
    return {
        "cards": [asdict(card) for card in ledger.cards],
        "rejected_count": ledger.rejected_count,
        "rejected_reasons": ledger.rejected_reasons[:20],
    }


def _limit_evidence_tiers(
    tiers: Dict[str, List[Dict[str, Any]]],
    *,
    per_tier: int = 8,
) -> Dict[str, List[Dict[str, Any]]]:
    return {
        key: [_compact_citation_for_prompt(citation) for citation in list(tiers.get(key, []))[:per_tier]]
        for key in EVIDENCE_TIER_KEYS
    }


def _citation_snippet_budget(citation: Dict[str, Any]) -> int:
    """正文类规范条文给更高字符预算，避免规范要点被截断丢失。"""
    role = str(citation.get("evidence_tier") or "").lower()
    if role == "code_spec":
        return PROMPT_SNIPPET_CHARS_CODE_SPEC
    return PROMPT_SNIPPET_CHARS


def _compact_citation_for_prompt(
    citation: Dict[str, Any], *, max_snippet_chars: Optional[int] = None
) -> Dict[str, Any]:
    budget = max_snippet_chars if max_snippet_chars is not None else _citation_snippet_budget(citation)
    # snippet 偏短时回落到更完整的 highlight_text，避免源头字段断链导致正文喂不进 LLM。
    raw = str(citation.get("snippet") or "")
    highlight = str(citation.get("highlight_text") or "")
    if len(highlight) > len(raw):
        raw = highlight
    snippet = re.sub(r"\s+", " ", raw).strip()
    if len(snippet) > budget:
        snippet = snippet[:budget] + "…"

    compact = {
        "source": citation.get("source"),
        "location": citation.get("location"),
        "page_number": citation.get("page_number"),
        "content_type": citation.get("content_type"),
        "snippet": snippet,
    }
    for key in ("chapter", "chapter_title", "sub_section", "attribute_type", "entity", "evidence_tier"):
        value = citation.get(key)
        if value:
            compact[key] = value
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def _format_citations_catalog(citations: List[Dict[str, Any]], max_snippet_chars: Optional[int] = None) -> str:
    lines: List[str] = []
    for idx, c in enumerate(citations, start=1):
        source = str(c.get("source") or "").strip() or "未知来源"
        location = str(c.get("location") or "").strip()
        budget = max_snippet_chars if max_snippet_chars is not None else _citation_snippet_budget(c)
        # 与 _compact_citation_for_prompt 一致：snippet 偏短时回落到更完整的 highlight_text。
        raw = str(c.get("snippet") or "")
        highlight = str(c.get("highlight_text") or "")
        if len(highlight) > len(raw):
            raw = highlight
        snippet = re.sub(r"\s+", " ", raw).strip()
        if len(snippet) > budget:
            snippet = snippet[: budget - 1] + "…"
        loc_part = f" | {location}" if location else ""
        snip_part = f" | {snippet}" if snippet else ""
        lines.append(f"[{idx}] {source}{loc_part}{snip_part}")
    return "\n".join(lines)
