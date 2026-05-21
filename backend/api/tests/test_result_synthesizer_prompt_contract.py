from pathlib import Path


def test_synthesizer_prompt_is_not_overconstrained():
    source = Path("backend/app/agents/result_synthesizer_agent/agent.py").read_text(encoding="utf-8")

    assert "必须提供“目录”" not in source
    assert "核心数据表格化" not in source
    assert "## 改进要求" not in source
