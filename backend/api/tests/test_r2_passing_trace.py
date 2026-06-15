"""链路快照提取纯函数测试：从 live 响应判定召回 vs 传递丢失。"""
from script.probe_r2_passing_trace import extract_source_trace


def test_extract_trace_flags_passing_loss():
    # 金标准在 worker 召回里出现，但在 final_citations 消失 -> 传递丢失
    resp = {
        "diagnostics": {
            "worker_recall": {
                "mongodb": ["GB 51039-2014.pdf", "noise.pdf"],
                "milvus": ["GB 51039-2014.pdf"],
            }
        },
        "citations": [{"source": "noise.pdf"}],
    }
    trace = extract_source_trace(resp, gold_keyword="GB 51039")
    assert trace["recalled"] is True
    assert trace["in_final"] is False
    assert trace["verdict"] == "passing_loss"


def test_extract_trace_flags_recall_miss():
    resp = {
        "diagnostics": {"worker_recall": {"mongodb": ["other.pdf"], "milvus": []}},
        "citations": [{"source": "other.pdf"}],
    }
    trace = extract_source_trace(resp, gold_keyword="GB 51039")
    assert trace["recalled"] is False
    assert trace["verdict"] == "recall_miss"


def test_extract_trace_present_throughout():
    resp = {
        "diagnostics": {"worker_recall": {"mongodb": ["GB 51039-2014.pdf"]}},
        "citations": [{"source": "GB 51039-2014.pdf"}],
    }
    trace = extract_source_trace(resp, gold_keyword="GB 51039")
    assert trace["recalled"] is True
    assert trace["in_final"] is True
    assert trace["verdict"] == "ok"
