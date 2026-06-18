from backend.app.agents import orchestrator_agent
from backend.app.agents.result_synthesizer_agent import agent as synthesizer_agent


def test_orchestrator_front_agent_temperature_is_zero(monkeypatch):
    captured = {}

    def fake_init_chat_model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("MEDIARCH_API_KEY", "test-key")
    monkeypatch.setattr(orchestrator_agent.agent, "init_chat_model", fake_init_chat_model)

    orchestrator_agent.agent._init_orchestrator_llm()

    assert captured["temperature"] == 0


def test_final_synthesizer_temperature_is_point_three(monkeypatch):
    captured = {}

    def fake_init_chat_model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("MEDIARCH_API_KEY", "test-key")
    monkeypatch.setattr(synthesizer_agent, "init_chat_model", fake_init_chat_model)

    synthesizer_agent._init_synthesizer_llm()

    assert captured["temperature"] == 0.3
