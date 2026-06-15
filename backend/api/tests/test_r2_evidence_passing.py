"""evidence_passing 模块：证据包构建纯函数测试。"""
from backend.app.agents import evidence_passing as ep


def test_compact_citation_uses_code_spec_budget():
    long_text = "条" * 500
    out = ep._compact_citation_for_prompt(
        {"source": "GB 51039", "snippet": long_text, "evidence_tier": "code_spec"}
    )
    # code_spec 预算 400，超出加省略号
    assert len(out["snippet"]) <= ep.PROMPT_SNIPPET_CHARS_CODE_SPEC + 1
    assert out["source"] == "GB 51039"


def test_compact_citation_falls_back_to_highlight_text():
    out = ep._compact_citation_for_prompt(
        {"source": "x", "snippet": "短", "highlight_text": "完整正文" * 10}
    )
    assert "完整正文" in out["snippet"]


def test_compact_citation_drops_empty_fields():
    out = ep._compact_citation_for_prompt({"source": "x", "snippet": "y", "location": ""})
    assert "location" not in out


def test_format_citations_catalog_numbers_sequentially():
    catalog = ep._format_citations_catalog(
        [{"source": "A", "snippet": "a"}, {"source": "B", "snippet": "b"}]
    )
    assert catalog.startswith("[1] A")
    assert "[2] B" in catalog


def test_limit_evidence_tiers_caps_per_tier():
    tiers = {k: [{"source": f"s{i}", "snippet": "x"} for i in range(10)] for k in ep.EVIDENCE_TIER_KEYS}
    out = ep._limit_evidence_tiers(tiers, per_tier=3)
    for key in ep.EVIDENCE_TIER_KEYS:
        assert len(out[key]) <= 3
